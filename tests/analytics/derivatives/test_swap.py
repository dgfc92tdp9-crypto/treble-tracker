"""`SWPM` — multi-curve, CSA-discounted swap valuation (spec §12.1).

Two tests here carry most of the weight.

:meth:`TestTheCurveRepricesItsOwnInputs.test_par_rate_reproduces_the_quote`
is the closest thing to external validation available without a licensed
reference: a swap written to match a curve input exactly is re-priced
cash flow by cash flow through code that shares nothing with the bootstrap's
residual function, and must return the quoted rate. Two independent
implementations agreeing to a fraction of a basis point is evidence; one
implementation agreeing with itself is not.

:class:`TestTheSingleCurveIdentityIsBroken` proves the pricer is not
secretly self-discounting — and proves it in the only way worth trusting,
by also showing the same assertion *pass* when the discount and forecast
curves are deliberately made the same. A test that cannot fail is not a
test (CLAUDE.md failure mode C).
"""

from __future__ import annotations

from datetime import date

import pytest

from tests.analytics.derivatives.conftest import (
    CALENDAR,
    EUR_CSA_NAME,
    FORECAST_NAME,
    FORECAST_QUOTES,
    NOTIONAL,
    OIS_NAME,
    SPOT,
    SWAP_MATURITY,
)
from treble.analytics.curves.config import InstrumentKind
from treble.analytics.curves.multicurve import CurveSet, UnknownCurveError
from treble.analytics.derivatives.csa import Collateral, CsaTerms
from treble.analytics.derivatives.swap import (
    NotionalStep,
    SwapPricingError,
    SwapSpec,
    price_swap,
    swap_bucketed_dv01,
    swap_dv01,
    swap_par_rate,
)
from treble.analytics.registry import ModelResult

TEN_YEAR = SWAP_MATURITY["10Y"]


def make_swap(maturity: date = TEN_YEAR, rate: float = 0.0420, **overrides: object) -> SwapSpec:
    """A USD market-convention trade: semiannual 30/360 vs quarterly ACT/360."""
    fields: dict[str, object] = {
        "notional": NOTIONAL,
        "fixed_rate": rate,
        "effective": SPOT,
        "maturity": maturity,
        "forecast_curve": FORECAST_NAME,
        "fixed_frequency": 2,
        "calendar": CALENDAR,
    }
    fields.update(overrides)
    return SwapSpec(**fields)  # type: ignore[arg-type]


class TestTheCurveRepricesItsOwnInputs:
    """The cross-check: bootstrap residual versus cash-flow pricer."""

    @pytest.mark.golden
    @pytest.mark.parametrize("tenor", ["2Y", "5Y", "10Y"])
    def test_par_rate_reproduces_the_quote(
        self, curves: CurveSet, usd_csa: CsaTerms, tenor: str
    ) -> None:
        """A trade matching a curve input must break even at its quote.

        The bootstrap solved the curve so that its own residual function
        saw a par rate equal to the quote. This walks the same instrument
        through `_schedule`, per-period forwards, per-period accruals and
        discount factors — a different code path — and asks for the par
        rate. Agreement to well under a basis point means the conventions
        match in both places, which is the only part of a swap that is hard.
        """
        quote = FORECAST_QUOTES[(InstrumentKind.SWAP, tenor)]
        par = price_swap(make_swap(SWAP_MATURITY[tenor], quote), curves, usd_csa).value.par_rate
        assert abs(par - quote) * 1e4 < 0.01, f"{tenor}: {(par - quote) * 1e4:+.4f}bp"

    def test_a_swap_struck_at_par_settles_at_zero(
        self, curves: CurveSet, usd_csa: CsaTerms
    ) -> None:
        par = swap_par_rate(make_swap(), curves, usd_csa).value
        priced = price_swap(make_swap(rate=par), curves, usd_csa).value
        assert priced.pv == pytest.approx(0.0, abs=1e-6)


class TestTheSingleCurveIdentityIsBroken:
    """The floating leg must not telescope to ``N x (D(0) - D(T))``."""

    @staticmethod
    def _telescope(curves: CurveSet, name: str) -> float:
        curve = curves.curve(name)
        return NOTIONAL * (curve.discount_at(SPOT) - curve.discount_at(TEN_YEAR))

    def test_the_float_leg_is_not_the_telescoped_value(
        self, curves: CurveSet, usd_csa: CsaTerms
    ) -> None:
        """Under a real basis the two differ by millions on $100m. A pricer
        that discounted off the forecast curve would land on the telescoped
        figure and look entirely ordinary doing it."""
        priced = price_swap(make_swap(), curves, usd_csa).value
        gap = priced.float_leg_pv - self._telescope(curves, OIS_NAME)
        assert abs(gap) > 0.01 * NOTIONAL, f"gap only {gap:,.0f} — is this self-discounting?"

    def test_the_identity_returns_when_the_two_curves_are_one(self, curves: CurveSet) -> None:
        """The other half of the previous test, and the reason to believe it.

        Discount the same trade at the *forecast* curve and the telescoping
        identity must hold exactly — same pricer, same trade, one input
        changed. If it did not hold here, the test above would be passing
        for some reason other than the one claimed.
        """
        self_discounted = CsaTerms(collateral_currency="USD", discount_curve=FORECAST_NAME)
        priced = price_swap(make_swap(), curves, self_discounted).value
        assert priced.float_leg_pv == pytest.approx(
            self._telescope(curves, FORECAST_NAME), rel=1e-12
        )


class TestTheCsaChangesTheNumber:
    """Spec 11.1: "not cosmetic; it moves a long-dated swap's PV materially"."""

    def test_the_same_trade_prices_differently_under_a_foreign_csa(
        self, eur_collateral_market: CurveSet, usd_csa: CsaTerms
    ) -> None:
        market = eur_collateral_market
        spec = make_swap(rate=0.0350)  # off-market, so the PV is large enough to compare
        usd = price_swap(spec, market, usd_csa).value.pv
        eur = price_swap(
            spec, market, CsaTerms(collateral_currency="EUR", discount_curve=EUR_CSA_NAME)
        ).value.pv
        assert usd != eur
        assert abs(eur - usd) > 1e-4 * abs(usd), (
            f"CSA moved the PV by only {abs(eur - usd):,.0f} on {abs(usd):,.0f} — "
            "the collateral agreement is not reaching the discounting"
        )

    def test_the_par_rate_depends_on_the_csa(
        self, eur_collateral_market: CurveSet, usd_csa: CsaTerms
    ) -> None:
        """The par rate is a ratio of two discounted quantities, so the
        collateral agreement is part of the economics rather than a
        presentation choice."""
        market = eur_collateral_market
        spec = make_swap()
        usd = swap_par_rate(spec, market, usd_csa).value
        eur = swap_par_rate(
            spec, market, CsaTerms(collateral_currency="EUR", discount_curve=EUR_CSA_NAME)
        ).value
        assert usd != eur

    def test_the_discount_curve_used_is_reported_beside_the_pv(
        self, curves: CurveSet, usd_csa: CsaTerms
    ) -> None:
        priced = price_swap(make_swap(), curves, usd_csa).value
        assert priced.discount_curve == OIS_NAME
        assert priced.forecast_curve == FORECAST_NAME
        assert priced.discount_curve_hash != priced.forecast_curve_hash
        assert priced.csa == "USD cash CSA · USD-SOFR-OIS"


class TestScheduleComesFromTheCurve:
    def test_float_frequency_follows_the_index_not_the_trade(
        self, curves: CurveSet, usd_csa: CsaTerms
    ) -> None:
        """A 3M index on a 10Y trade is 40 floating periods. There is no
        trade field that could say otherwise, which is the point: a
        semiannual schedule on a quarterly curve would take every forward
        over the wrong period and still produce a plausible PV."""
        flows = price_swap(make_swap(), curves, usd_csa).value.cashflows
        assert sum(1 for f in flows if f.leg == "float") == 40
        assert sum(1 for f in flows if f.leg == "fixed") == 20

    def test_discount_factors_fall_with_maturity(self, curves: CurveSet, usd_csa: CsaTerms) -> None:
        flows = price_swap(make_swap(), curves, usd_csa).value.cashflows
        for leg in ("fixed", "float"):
            factors = [f.discount_factor for f in flows if f.leg == leg]
            assert factors == sorted(factors, reverse=True)
            assert all(0.0 < d < 1.0 for d in factors)

    def test_every_flow_carries_the_inputs_that_made_it(
        self, curves: CurveSet, usd_csa: CsaTerms
    ) -> None:
        """`SWPM` shows cash flow schedules, and a schedule that shows only
        amounts cannot be checked against a confirmation."""
        for flow in price_swap(make_swap(), curves, usd_csa).value.cashflows:
            assert flow.accrual_end > flow.accrual_start
            assert flow.accrual > 0.0
            assert flow.present_value == pytest.approx(flow.amount * flow.discount_factor)
            assert flow.amount == pytest.approx(flow.notional * flow.rate * flow.accrual)


class TestPosition:
    def test_paying_and_receiving_are_opposite(self, curves: CurveSet, usd_csa: CsaTerms) -> None:
        payer = price_swap(make_swap(rate=0.0350), curves, usd_csa).value
        receiver = price_swap(make_swap(rate=0.0350, pay_fixed=False), curves, usd_csa).value
        assert payer.pv == pytest.approx(-receiver.pv)

    def test_a_below_market_fixed_rate_favours_the_payer(
        self, curves: CurveSet, usd_csa: CsaTerms
    ) -> None:
        par = swap_par_rate(make_swap(), curves, usd_csa).value
        assert price_swap(make_swap(rate=par - 0.0050), curves, usd_csa).value.pv > 0.0

    def test_a_contractual_spread_raises_the_floating_leg(
        self, curves: CurveSet, usd_csa: CsaTerms
    ) -> None:
        base = price_swap(make_swap(), curves, usd_csa).value
        wider = price_swap(make_swap(float_spread=0.0025), curves, usd_csa).value
        assert wider.float_leg_pv > base.float_leg_pv
        assert wider.fixed_leg_pv == pytest.approx(base.fixed_leg_pv)


class TestRisk:
    def test_a_payer_gains_when_rates_rise(self, curves: CurveSet, usd_csa: CsaTerms) -> None:
        assert swap_dv01(make_swap(), curves, usd_csa).value > 0.0

    def test_the_sign_follows_the_position(self, curves: CurveSet, usd_csa: CsaTerms) -> None:
        """Reported signed rather than as a magnitude: an absolute value
        would hide which side of the trade this book is on."""
        payer = swap_dv01(make_swap(), curves, usd_csa).value
        receiver = swap_dv01(make_swap(pay_fixed=False), curves, usd_csa).value
        assert payer == pytest.approx(-receiver, rel=1e-9)

    def test_dv01_scales_with_maturity(self, curves: CurveSet, usd_csa: CsaTerms) -> None:
        short = swap_dv01(make_swap(SWAP_MATURITY["2Y"], 0.0420), curves, usd_csa).value
        long = swap_dv01(make_swap(SWAP_MATURITY["10Y"], 0.0420), curves, usd_csa).value
        assert long > 3.0 * short

    def test_buckets_cover_the_discount_curve_too(
        self, curves: CurveSet, usd_csa: CsaTerms
    ) -> None:
        """A swap discounted at OIS has genuine OIS exposure. A ladder
        showing only the forecast curve would leave it unhedged while
        appearing complete."""
        buckets = swap_bucketed_dv01(make_swap(rate=0.0350), curves, usd_csa).value
        by_curve = {b.curve for b in buckets}
        assert by_curve == {OIS_NAME, FORECAST_NAME}
        assert any(abs(b.dv01) > 1.0 for b in buckets if b.curve == OIS_NAME)

    def test_buckets_sum_to_about_the_parallel_dv01(
        self, curves: CurveSet, usd_csa: CsaTerms
    ) -> None:
        """Not exactly: bumping one node re-solves the interpolation around
        it, and the cross terms are real. Agreement to a fraction of a
        percent is what a correct bucketing gives; a bucketing that missed a
        curve would be out by far more."""
        spec = make_swap(rate=0.0350)
        parallel = swap_dv01(spec, curves, usd_csa).value
        total = sum(b.dv01 for b in swap_bucketed_dv01(spec, curves, usd_csa).value)
        assert total == pytest.approx(parallel, rel=5e-3)


class TestAmortising:
    def test_the_notional_steps_down_on_schedule(self, curves: CurveSet, usd_csa: CsaTerms) -> None:
        spec = make_swap(
            amortisation=(NotionalStep(effective=date(2031, 7, 2), notional=NOTIONAL / 2),)
        )
        flows = price_swap(spec, curves, usd_csa).value.cashflows
        early = [f.notional for f in flows if f.accrual_start < date(2031, 7, 2)]
        late = [f.notional for f in flows if f.accrual_start >= date(2031, 7, 2)]
        assert set(early) == {NOTIONAL}
        assert set(late) == {NOTIONAL / 2}

    def test_amortising_reduces_the_annuity(self, curves: CurveSet, usd_csa: CsaTerms) -> None:
        full = price_swap(make_swap(), curves, usd_csa).value.annuity
        amortised = price_swap(
            make_swap(
                amortisation=(NotionalStep(effective=date(2031, 7, 2), notional=NOTIONAL / 2),)
            ),
            curves,
            usd_csa,
        ).value.annuity
        assert 0.5 * full < amortised < full

    def test_steps_out_of_order_are_refused(self) -> None:
        with pytest.raises(ValueError, match="strict date order"):
            make_swap(
                amortisation=(
                    NotionalStep(effective=date(2033, 7, 2), notional=5e7),
                    NotionalStep(effective=date(2031, 7, 2), notional=8e7),
                )
            )


class TestRefusals:
    def test_a_seasoned_swap_needs_its_fixings(self, curves: CurveSet, usd_csa: CsaTerms) -> None:
        """The first period has already fixed. Projecting it from the curve
        would invent a rate that was in fact published."""
        with pytest.raises(SwapPricingError, match="already fixed"):
            price_swap(make_swap(effective=date(2025, 7, 2)), curves, usd_csa)

    def test_an_overnight_curve_cannot_forecast_an_index_leg(
        self, curves: CurveSet, usd_csa: CsaTerms
    ) -> None:
        """An overnight index compounds daily within each period; scheduling
        it as discrete index periods would value a different instrument."""
        with pytest.raises(SwapPricingError, match="forecasts no index tenor"):
            price_swap(make_swap(forecast_curve=OIS_NAME), curves, usd_csa)

    def test_an_unknown_forecast_curve_is_named(self, curves: CurveSet, usd_csa: CsaTerms) -> None:
        with pytest.raises(UnknownCurveError):
            price_swap(make_swap(forecast_curve="USD-LIBOR-6M"), curves, usd_csa)

    def test_a_swap_maturing_before_it_starts_is_refused(self) -> None:
        with pytest.raises(ValueError, match="matures after it starts"):
            make_swap(maturity=date(2026, 1, 1))

    def test_a_fixed_frequency_that_does_not_divide_the_year_is_refused(self) -> None:
        with pytest.raises(ValueError, match="whole months"):
            make_swap(fixed_frequency=5)


class TestModelIdentity:
    def test_the_envelope_pins_the_whole_curve_environment(
        self, curves: CurveSet, usd_csa: CsaTerms
    ) -> None:
        """I3 plus I4: the result records the content hash of the curve set,
        so "which curves produced this" is answerable from the output rather
        than from remembering how it was called."""
        result = price_swap(make_swap(), curves, usd_csa)
        assert isinstance(result, ModelResult)
        assert result.model_id == "derivatives.price_swap"
        assert result.inputs["curves"] == curves.content_hash

    def test_every_swap_analytic_is_enveloped(self, curves: CurveSet, usd_csa: CsaTerms) -> None:
        spec = make_swap()
        for analytic in (swap_par_rate, swap_dv01, swap_bucketed_dv01):
            assert isinstance(analytic(spec, curves, usd_csa), ModelResult)


class TestCsaResolution:
    def test_an_unknown_collateral_curve_refuses_rather_than_falling_back(
        self, curves: CurveSet
    ) -> None:
        """The failure this whole module exists to prevent: a swap asked to
        price under one CSA, quietly discounted at another."""
        csa = CsaTerms(collateral_currency="EUR", discount_curve="EUR-ESTR-OIS")
        with pytest.raises(UnknownCurveError, match="Refusing rather than discounting"):
            price_swap(make_swap(), curves, csa)

    def test_a_threshold_is_refused_not_ignored(self, curves: CurveSet) -> None:
        """Exposure below the threshold is uncollateralised, so the trade is
        not funded at the collateral rate over its whole life."""
        csa = CsaTerms(collateral_currency="USD", discount_curve=OIS_NAME, threshold=10_000_000.0)
        with pytest.raises(NotImplementedError, match="XVA"):
            price_swap(make_swap(), curves, csa)

    def test_an_uncollateralised_trade_says_so(self, curves: CurveSet) -> None:
        csa = CsaTerms(
            collateral_currency="USD",
            discount_curve=OIS_NAME,
            collateral=Collateral.NONE,
        )
        priced = price_swap(make_swap(), curves, csa).value
        assert priced.csa.startswith("Uncollateralised")

    def test_a_minimum_transfer_amount_does_not_change_the_discounting(
        self, curves: CurveSet, usd_csa: CsaTerms
    ) -> None:
        """An MTA changes when collateral moves, not what rate it earns.
        Recorded on the terms, absent from the calculation — a documented
        decision rather than an oversight."""
        with_mta = CsaTerms(
            collateral_currency="USD",
            discount_curve=OIS_NAME,
            minimum_transfer_amount=250_000.0,
        )
        assert (
            price_swap(make_swap(), curves, with_mta).value.pv
            == price_swap(make_swap(), curves, usd_csa).value.pv
        )
