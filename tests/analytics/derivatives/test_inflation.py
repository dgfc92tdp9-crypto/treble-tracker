"""Zero-coupon inflation swaps (spec §12.1).

The anchor is the breakeven: priced at its own breakeven rate the swap must
be worth exactly zero, and that ties the compounded fixed leg to the index
ratio rather than checking either one against a number computed the same
way. It is also where a simple-rate shortcut shows up, because the two
agree at one year and diverge with maturity.
"""

from __future__ import annotations

import pytest

from treble.analytics.derivatives.inflation import InflationSwapSpec, price_inflation_swap

_price = price_inflation_swap.__wrapped__  # type: ignore[attr-defined]

BASE_INDEX = 300.0


def _spec(rate: float, years: float = 10.0, lag: int = 3) -> InflationSwapSpec:
    return InflationSwapSpec(
        notional=10_000_000.0,
        fixed_rate=rate,
        maturity_years=years,
        index_lag_months=lag,
    )


class TestTheBreakeven:
    @pytest.mark.parametrize("years", [1.0, 5.0, 10.0, 30.0])
    def test_priced_at_its_own_breakeven_the_swap_is_worth_nothing(self, years: float) -> None:
        """Ties the compounded fixed leg to the index ratio. Thirty years is
        in the list deliberately: a simple-rate fixed leg agrees at one year
        and is wrong by a factor at thirty."""
        projected = BASE_INDEX * (1.03**years)
        first = _price(
            _spec(0.0, years),
            base_index=BASE_INDEX,
            projected_index=projected,
            discount_factor=1.0,
        )
        fair = _price(
            _spec(first.breakeven_rate, years),
            base_index=BASE_INDEX,
            projected_index=projected,
            discount_factor=1.0,
        )
        assert fair.value == pytest.approx(0.0, abs=1e-6)
        assert first.breakeven_rate == pytest.approx(0.03)

    def test_the_fixed_leg_compounds_rather_than_multiplying(self) -> None:
        """At 3% over 30 years the compounded leg is ~143% and a simple one
        ~90%. Not a rounding matter, and this is where it bites."""
        priced = _price(
            _spec(0.03, 30.0),
            base_index=BASE_INDEX,
            projected_index=BASE_INDEX,
            discount_factor=1.0,
        )
        assert priced.fixed_leg == pytest.approx((1.03**30 - 1) * 10_000_000.0)
        assert priced.fixed_leg > 1.4 * 10_000_000.0


class TestTheLegs:
    def test_realised_above_the_fixed_rate_pays_the_inflation_receiver(self) -> None:
        priced = _price(
            _spec(0.02),
            base_index=BASE_INDEX,
            projected_index=BASE_INDEX * (1.03**10),
            discount_factor=1.0,
        )
        assert priced.value > 0.0
        assert priced.inflation_leg > priced.fixed_leg

    def test_the_payer_has_the_opposite_value(self) -> None:
        kw = {
            "base_index": BASE_INDEX,
            "projected_index": BASE_INDEX * (1.03**10),
            "discount_factor": 1.0,
        }
        receiver = _price(_spec(0.02), **kw)
        payer = _price(_spec(0.02), **kw, receive_inflation=False)
        assert payer.value == pytest.approx(-receiver.value)

    def test_deflation_is_not_floored(self) -> None:
        """A plain zero-coupon swap embeds no floor. Adding one would price
        a different contract -- TIPS floor their principal, this does not."""
        priced = _price(
            _spec(0.02),
            base_index=BASE_INDEX,
            projected_index=BASE_INDEX * 0.9,
            discount_factor=1.0,
        )
        assert priced.inflation_leg < 0.0
        assert priced.value < 0.0

    def test_discounting_scales_both_legs(self) -> None:
        kw = {"base_index": BASE_INDEX, "projected_index": BASE_INDEX * 1.3}
        full = _price(_spec(0.02), **kw, discount_factor=1.0)
        half = _price(_spec(0.02), **kw, discount_factor=0.5)
        assert half.value == pytest.approx(full.value * 0.5)
        assert half.fixed_leg == pytest.approx(full.fixed_leg * 0.5)


class TestTheLagTravels:
    @pytest.mark.parametrize("lag", [2, 3, 8])
    def test_the_index_lag_reaches_the_result(self, lag: int) -> None:
        """US CPI trades at three months, UK RPI at two or eight. A screen
        that cannot say which lag its number assumes is showing a breakeven
        for an unstated contract."""
        priced = _price(
            _spec(0.02, lag=lag),
            base_index=BASE_INDEX,
            projected_index=BASE_INDEX * 1.3,
            discount_factor=1.0,
        )
        assert priced.index_lag_months == lag

    def test_the_lag_must_be_stated(self) -> None:
        """Required rather than defaulted: a wrong lag measures a different
        period from the one the contract pays on."""
        with pytest.raises(Exception, match="index_lag_months"):
            InflationSwapSpec(notional=1.0, fixed_rate=0.02, maturity_years=10.0)  # type: ignore[call-arg]


class TestItRefusesBadInput:
    def test_a_non_positive_base_index_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no basis for a ratio"):
            _price(_spec(0.02), base_index=0.0, projected_index=BASE_INDEX, discount_factor=1.0)

    def test_a_non_positive_projected_index_is_refused(self) -> None:
        with pytest.raises(ValueError, match="data error"):
            _price(_spec(0.02), base_index=BASE_INDEX, projected_index=0.0, discount_factor=1.0)
