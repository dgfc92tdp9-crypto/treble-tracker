"""Cross-currency swaps (spec §12.1).

Three things carry the risk: the notional exchange, which is what makes this
different from a single-currency basis swap; the FX conversion, which must
scale the whole foreign leg rather than only its coupons; and the side the
basis spread sits on, which has the right magnitude and the wrong sign if it
goes on the wrong leg.

Each is pinned by a value, not only by a direction, after a session in which
six behavioural suites turned out to constrain nothing.
"""

from __future__ import annotations

import pytest

from treble.analytics.derivatives.crosscurrency import (
    CrossCurrencySpec,
    price_cross_currency_swap,
)

_price = price_cross_currency_swap.__wrapped__  # type: ignore[attr-defined]

SPOT = 1.25  # domestic per foreign


def _spec(basis: float = -0.0015, resettable: bool = False) -> CrossCurrencySpec:
    return CrossCurrencySpec(
        domestic_notional=125_000_000.0,
        foreign_notional=100_000_000.0,
        basis_spread=basis,
        resettable=resettable,
    )


MARKET = {
    "spot": SPOT,
    "domestic_float_pv": 0.10,
    "foreign_float_pv": 0.10,
    "domestic_final_df": 0.90,
    "foreign_final_df": 0.90,
    "foreign_annuity": 5.0,
}


class TestTheNotionalExchange:
    def test_the_final_exchange_is_in_the_value(self) -> None:
        """The distinguishing feature. Without the returned notional the
        domestic leg would be 12.5m rather than 125m, so this pins the
        magnitude and not merely the sign."""
        priced = _price(_spec(basis=0.0), **MARKET)
        assert priced.domestic_leg == pytest.approx(125_000_000.0 * (0.10 + 0.90))
        assert priced.foreign_leg == pytest.approx(100_000_000.0 * 1.00 * SPOT)

    def test_a_matched_trade_at_zero_basis_is_worth_nothing(self) -> None:
        """Identical curves, notionals struck at spot, no basis: the two
        legs must cancel exactly."""
        priced = _price(_spec(basis=0.0), **MARKET)
        assert priced.value == pytest.approx(0.0)


class TestFx:
    def test_spot_scales_the_whole_foreign_leg(self) -> None:
        """Coupons and returned notional alike. Converting only the coupons
        is the error that leaves the notional exchange unhedged."""
        base = _price(_spec(basis=0.0), **MARKET)
        stronger = _price(_spec(basis=0.0), **{**MARKET, "spot": SPOT * 1.10})
        assert stronger.foreign_leg == pytest.approx(base.foreign_leg * 1.10)

    def test_a_stronger_foreign_currency_costs_the_domestic_receiver(self) -> None:
        base = _price(_spec(basis=0.0), **MARKET)
        stronger = _price(_spec(basis=0.0), **{**MARKET, "spot": SPOT * 1.10})
        assert stronger.value < base.value

    def test_the_payer_has_the_opposite_value(self) -> None:
        kw = {**MARKET, "spot": SPOT * 1.10}
        receiver = _price(_spec(), **kw)
        payer = _price(_spec(), **kw, receive_domestic=False)
        assert payer.value == pytest.approx(-receiver.value)


class TestTheBasisSpread:
    def test_it_accrues_on_the_foreign_leg_at_its_own_notional(self) -> None:
        """Pinned by value. A spread applied to the domestic leg has the
        right magnitude and the wrong sign in the P&L, which is the error
        most likely to survive a review because every number still looks
        reasonable."""
        priced = _price(_spec(basis=-0.0015), **MARKET)
        expected = 100_000_000.0 * -0.0015 * 5.0 * SPOT
        assert priced.basis_value == pytest.approx(expected)

    def test_a_negative_basis_cheapens_the_foreign_leg(self) -> None:
        flat = _price(_spec(basis=0.0), **MARKET)
        negative = _price(_spec(basis=-0.0015), **MARKET)
        assert negative.foreign_leg < flat.foreign_leg
        assert negative.value > flat.value

    def test_the_basis_is_the_only_difference_when_curves_match(self) -> None:
        """Isolates it: with identical curves and notionals at spot, the
        whole value is the basis."""
        priced = _price(_spec(basis=-0.0015), **MARKET)
        assert priced.value == pytest.approx(-priced.basis_value)


class TestItRefusesRatherThanApproximates:
    def test_a_resettable_trade_is_refused(self) -> None:
        """A mark-to-market cross-currency swap resets its foreign notional
        to spot each period, which removes most of the FX exposure between
        resets. Pricing it here would overstate that exposure rather than
        approximate it."""
        with pytest.raises(ValueError, match="fixed-notional form"):
            _price(_spec(resettable=True), **MARKET)

    def test_a_non_positive_spot_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a free currency"):
            _price(_spec(), **{**MARKET, "spot": 0.0})
