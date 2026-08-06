"""Tenor basis swaps — float versus float (spec §12.1).

The three properties that matter are all about what a wrong implementation
would look like rather than about the arithmetic:

- Two legs on the *same* curve must price to a zero spread exactly. Any other
  answer is the engine finding a basis where there is none.
- Both legs must discount on the **CSA** curve. A leg discounted on its own
  forecast curve is worth `N x (D(0) - D(T))` on that curve whatever its
  tenor, so self-discounting both legs collapses the basis to nearly nothing
  — the single-curve error the whole framework exists to prevent.
- Which leg carries the spread is a property of the trade, not a convention
  buried in the pricer. Swapping the legs must flip the sign.
"""

from __future__ import annotations

from datetime import date

import pytest

from tests.analytics.derivatives.conftest import (
    AS_OF,
    CALENDAR,
    FORECAST_NAME,
    NOTIONAL,
    OIS_CONFIG,
    OIS_NAME,
    OIS_QUOTES,
    SPOT,
    SWAP_MATURITY,
)
from treble.analytics._ql import DayCount
from treble.analytics.curves.config import CurveConfig, InstrumentKind, InstrumentSpec
from treble.analytics.curves.multicurve import CurveSet, CurveSpec, UnknownCurveError
from treble.analytics.derivatives.basis import BasisSwapSpec, basis_par_spread, price_basis_swap
from treble.analytics.derivatives.csa import CsaTerms
from treble.analytics.derivatives.swap import SwapPricingError

TEN_YEAR = SWAP_MATURITY["10Y"]
SIX_MONTH_NAME = "USD-LIBOR-6M"

_price = price_basis_swap.__wrapped__
_spread = basis_par_spread.__wrapped__

#: A second term curve, quoted above the 3M curve so a real basis exists.
#: Built here rather than in conftest because only these tests need two
#: forecast tenors, and a fixture nothing else uses belongs with its tests.
_SIX_MONTH_QUOTES = {
    (InstrumentKind.DEPOSIT, "6M"): 0.0465,
    (InstrumentKind.SWAP, "2Y"): 0.0432,
    (InstrumentKind.SWAP, "5Y"): 0.0417,
    (InstrumentKind.SWAP, "10Y"): 0.0432,
}

_SIX_MONTH_CONFIG = CurveConfig(
    name=SIX_MONTH_NAME,
    currency="USD",
    calendar=CALENDAR,
    index_tenor="6M",
    discount_basis=OIS_NAME,
    swap_fixed_frequency=2,
    fixed_leg_day_count=DayCount.THIRTY_360,
    float_leg_day_count=DayCount.ACT_360,
    instruments=(
        InstrumentSpec(kind=InstrumentKind.DEPOSIT, tenor="6M"),
        InstrumentSpec(kind=InstrumentKind.SWAP, tenor="2Y"),
        InstrumentSpec(kind=InstrumentKind.SWAP, tenor="5Y"),
        InstrumentSpec(kind=InstrumentKind.SWAP, tenor="10Y"),
    ),
)


@pytest.fixture(scope="module")
def basis_curves() -> CurveSet:
    """OIS discounting with both a 3M and a 6M forecast curve."""
    from tests.analytics.derivatives.conftest import FORECAST_CONFIG, FORECAST_QUOTES

    return CurveSet(
        AS_OF,
        [
            CurveSpec(FORECAST_CONFIG, FORECAST_QUOTES),
            CurveSpec(_SIX_MONTH_CONFIG, _SIX_MONTH_QUOTES),
            CurveSpec(OIS_CONFIG, OIS_QUOTES),
        ],
    )


@pytest.fixture(scope="module")
def csa() -> CsaTerms:
    return CsaTerms(collateral_currency="USD", discount_curve=OIS_NAME)


def _swap(pay: str, receive: str, **kwargs: object) -> BasisSwapSpec:
    return BasisSwapSpec(
        notional=NOTIONAL,
        effective=SPOT,
        maturity=TEN_YEAR,
        pay_curve=pay,
        receive_curve=receive,
        **kwargs,  # type: ignore[arg-type]
    )


class TestTheIdentity:
    def test_the_same_curve_on_both_legs_has_no_basis(
        self, basis_curves: CurveSet, csa: CsaTerms
    ) -> None:
        """Exactly zero, not nearly. Any other answer is the engine finding a
        basis where there is none."""
        priced = _price(_swap(FORECAST_NAME, FORECAST_NAME), basis_curves, csa)
        assert priced.pv == pytest.approx(0.0, abs=1e-9)
        assert priced.par_spread == pytest.approx(0.0, abs=1e-12)

    def test_a_trade_at_its_par_spread_is_worth_nothing(
        self, basis_curves: CurveSet, csa: CsaTerms
    ) -> None:
        spec = _swap(FORECAST_NAME, SIX_MONTH_NAME)
        par = _spread(spec, basis_curves, csa)
        at_par = spec.model_copy(update={"pay_spread": par})
        assert _price(at_par, basis_curves, csa).pv == pytest.approx(0.0, abs=1e-6)


class TestWhereTheSpreadSits:
    def test_paying_the_shorter_tenor_earns_a_positive_spread(
        self, basis_curves: CurveSet, csa: CsaTerms
    ) -> None:
        """The 6M curve is quoted above the 3M curve here, so a book paying
        3M and receiving 6M must be paid for it."""
        assert _spread(_swap(FORECAST_NAME, SIX_MONTH_NAME), basis_curves, csa) > 0

    def test_swapping_the_legs_flips_the_sign(self, basis_curves: CurveSet, csa: CsaTerms) -> None:
        """Which leg carries the spread is the trade's, not a convention
        buried in the pricer."""
        forward = _spread(_swap(FORECAST_NAME, SIX_MONTH_NAME), basis_curves, csa)
        reverse = _spread(_swap(SIX_MONTH_NAME, FORECAST_NAME), basis_curves, csa)
        assert forward > 0 > reverse

    def test_the_two_directions_are_consistent_through_their_annuities(
        self, basis_curves: CurveSet, csa: CsaTerms
    ) -> None:
        """They are not equal and opposite: the spread is quoted per leg, and
        the two legs have different annuities. The PV each earns must match."""
        forward = _price(_swap(FORECAST_NAME, SIX_MONTH_NAME), basis_curves, csa)
        reverse = _price(_swap(SIX_MONTH_NAME, FORECAST_NAME), basis_curves, csa)
        assert forward.par_spread * forward.spread_annuity == pytest.approx(
            -reverse.par_spread * reverse.spread_annuity, rel=1e-9
        )


class TestDiscounting:
    def test_both_legs_discount_on_the_csa_curve_not_their_own(
        self, basis_curves: CurveSet, csa: CsaTerms
    ) -> None:
        """The check that separates a multi-curve engine from a single-curve
        one. A leg discounted on its own forecast curve is worth
        `N x (D(0) - D(T))` on that curve regardless of tenor, so both legs
        would collapse to the same value and the basis would vanish.
        """
        priced = _price(_swap(FORECAST_NAME, SIX_MONTH_NAME), basis_curves, csa)
        for name, leg_pv in (
            (FORECAST_NAME, priced.pay_leg_pv),
            (SIX_MONTH_NAME, priced.receive_leg_pv),
        ):
            own = basis_curves.curve(name)
            self_discounted = NOTIONAL * (own.discount_at(SPOT) - own.discount_at(TEN_YEAR))
            assert leg_pv != pytest.approx(self_discounted, rel=1e-6), (
                f"{name} leg was discounted on its own curve, which makes the basis vanish"
            )
        assert priced.discount_curve == OIS_NAME

    def test_the_basis_survives_the_correct_discounting(
        self, basis_curves: CurveSet, csa: CsaTerms
    ) -> None:
        """A positive, material spread rather than a rounding artefact."""
        assert _spread(_swap(FORECAST_NAME, SIX_MONTH_NAME), basis_curves, csa) * 1e4 > 1.0


class TestRefusals:
    def test_an_unknown_curve_is_named(self, basis_curves: CurveSet, csa: CsaTerms) -> None:
        with pytest.raises(UnknownCurveError):
            _price(_swap("USD-LIBOR-12M", SIX_MONTH_NAME), basis_curves, csa)

    def test_an_overnight_leg_without_a_frequency_is_refused(
        self, basis_curves: CurveSet, csa: CsaTerms
    ) -> None:
        """An overnight index has no tenor to schedule from, exactly as on a
        vanilla swap."""
        with pytest.raises(SwapPricingError, match="no tenor to schedule from"):
            _price(_swap(OIS_NAME, SIX_MONTH_NAME), basis_curves, csa)

    def test_an_overnight_leg_with_a_stated_frequency_prices(
        self, basis_curves: CurveSet, csa: CsaTerms
    ) -> None:
        """An OIS-versus-EURIBOR basis is a real trade; what it needed was a
        schedule, not a refusal."""
        priced = _price(_swap(OIS_NAME, SIX_MONTH_NAME, pay_frequency=1), basis_curves, csa)
        assert priced.par_spread > 0
        # The overnight leg compounds; the term leg does not. Both are
        # "float", so this is the only thing that tells them apart.
        assert all(flow.compounded for flow in priced.pay_cashflows)
        assert not any(flow.compounded for flow in priced.receive_cashflows)

    def test_a_seasoned_trade_needs_its_fixings(
        self, basis_curves: CurveSet, csa: CsaTerms
    ) -> None:
        spec = _swap(FORECAST_NAME, SIX_MONTH_NAME).model_copy(
            update={"effective": date(2026, 1, 2)}
        )
        with pytest.raises(SwapPricingError, match="already fixed"):
            _price(spec, basis_curves, csa)

    def test_a_swap_maturing_before_it_starts_is_refused(self) -> None:
        with pytest.raises(ValueError, match="matures after it starts"):
            BasisSwapSpec(
                notional=NOTIONAL,
                effective=SPOT,
                maturity=date(2026, 1, 1),
                pay_curve=FORECAST_NAME,
                receive_curve=SIX_MONTH_NAME,
            )


class TestTheLegsAreTheVanillaPricersLegs:
    def test_a_basis_leg_matches_the_vanilla_swaps_float_leg(
        self, basis_curves: CurveSet, csa: CsaTerms
    ) -> None:
        """Two implementations of a floating leg would disagree by an amount
        small enough to read as rounding. There is one."""
        from treble.analytics.derivatives.swap import SwapSpec, price_swap

        vanilla = price_swap.__wrapped__(
            SwapSpec(
                notional=NOTIONAL,
                fixed_rate=0.0,
                effective=SPOT,
                maturity=TEN_YEAR,
                forecast_curve=SIX_MONTH_NAME,
                calendar=CALENDAR,
            ),
            basis_curves,
            csa,
        )
        priced = _price(_swap(FORECAST_NAME, SIX_MONTH_NAME), basis_curves, csa)
        assert priced.receive_leg_pv == pytest.approx(vanilla.float_leg_pv, rel=1e-12)

    def test_the_two_specs_default_to_the_same_calendar(self) -> None:
        """Found by the test above disagreeing by 0.03 on 35.6m. A basis swap
        and a vanilla swap built with default calendars must schedule the
        same way, or the same leg is worth different amounts in the two and
        the gap reads as a modelling error rather than as a holiday."""
        from treble.analytics.derivatives.swap import SwapSpec

        assert (
            BasisSwapSpec.model_fields["calendar"].default
            == SwapSpec.model_fields["calendar"].default
        )

    def test_the_two_legs_are_kept_apart(self, basis_curves: CurveSet, csa: CsaTerms) -> None:
        """Both legs are floating. Merged into one schedule every row reads
        "float" with no way to tell which side it is, and the difference
        between the sides is the entire content of a basis swap."""
        priced = _price(_swap(FORECAST_NAME, SIX_MONTH_NAME), basis_curves, csa)
        assert priced.pay_cashflows and priced.receive_cashflows
        assert len(priced.pay_cashflows) != len(priced.receive_cashflows), (
            "a 3M leg and a 6M leg pay at different frequencies"
        )
        assert sum(f.present_value for f in priced.pay_cashflows) == pytest.approx(
            priced.pay_leg_pv
        )
