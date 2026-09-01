"""Recovering a spread from a traded upfront.

Standard CDS trade at a fixed coupon — 100bp or 500bp — with a payment at
settlement, so the market's view of credit reaches the tape as *points
upfront* on most prints and as a spread on roughly one in ten. This inverts
`price_cds` to read the first as the second.

The test that carries the file is `TestARoundTrip`: a spread priced to an
upfront and solved back must return the spread it started from. Everything
else checks a way the solve could be confidently wrong.
"""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from treble.analytics.credit.cds import (
    HAZARD_CEILING,
    SOLVE_STEPS,
    CdsSpec,
    UpfrontOutOfRangeError,
    hazard_from_spread,
    hazard_from_upfront,
    par_spread,
    price_cds,
    spread_from_upfront,
)

DISCOUNT = 0.04
NOTIONAL = 10_000_000.0


def _spec(coupon: float = 0.05, years: int = 5) -> CdsSpec:
    return CdsSpec(
        notional=NOTIONAL,
        coupon=coupon,
        trade_date=date(2026, 9, 1),
        maturity=date(2026 + years, 9, 1),
    )


def _upfront_for(spread: float, spec: CdsSpec) -> float:
    """Price a contract at a given spread and return what settles."""
    hazard = hazard_from_spread(spread).value
    return price_cds(spec, hazard, DISCOUNT).value.upfront


class TestARoundTrip:
    """Price a spread to an upfront, solve it back, get the spread."""

    @pytest.mark.parametrize("hazard", [0.005, 0.02, 0.09367, 0.25, 1.0])
    def test_the_hazard_survives_the_journey_exactly(self, hazard: float) -> None:
        """The exact property. Price a hazard to an upfront, solve it back,
        and the same hazard returns — no approximation anywhere in the
        loop, so this is the solve's own accuracy and nothing else's."""
        spec = _spec()
        upfront = price_cds(spec, hazard, DISCOUNT).value.upfront
        assert hazard_from_upfront(spec, upfront, DISCOUNT).value == pytest.approx(
            hazard, rel=1e-12
        )

    @pytest.mark.parametrize("spread", [0.005, 0.01, 0.03, 0.0575, 0.12, 0.30])
    def test_the_spread_survives_it_to_within_the_triangle(self, spread: float) -> None:
        """Not exact, and it should not be — but the error is *characterised*
        rather than merely bounded.

        `hazard_from_spread` is the credit triangle `h = s / (1 - R)` and
        `par_spread` reads the legs, so a round trip through both carries the
        accrued-on-default term the triangle omits. Measured across
        50bp-3,000bp the implied spread sits consistently **0.37% to 0.51%
        above** the input — the triangle understates the hazard by about half
        a percent, everywhere.

        Asserting the band rather than a loose tolerance means a change to
        either model shows up here as a number that moved, instead of
        passing silently inside a generous margin.
        """
        spec = _spec()
        implied = spread_from_upfront(spec, _upfront_for(spread, spec), DISCOUNT).value
        relative = (implied - spread) / spread
        assert 0.003 < relative < 0.006, f"the triangle's gap moved: {relative:.4%}"

    @pytest.mark.parametrize("coupon", [0.01, 0.05])
    @pytest.mark.parametrize("years", [1, 3, 5, 10])
    def test_it_holds_across_the_standard_contracts(self, coupon: float, years: int) -> None:
        spec = _spec(coupon=coupon, years=years)
        implied = spread_from_upfront(spec, _upfront_for(0.04, spec), DISCOUNT).value
        assert implied == pytest.approx(0.04, abs=2e-3)

    def test_the_solved_hazard_reprices_the_upfront_exactly(self) -> None:
        """The solve's own accuracy, separate from the triangle's."""
        spec = _spec()
        observed = 0.02377 * NOTIONAL
        hazard = hazard_from_upfront(spec, observed, DISCOUNT).value
        assert price_cds(spec, hazard, DISCOUNT).value.upfront == pytest.approx(observed, abs=1e-6)


class TestTheInvariantThatNeedsNoData:
    """A contract settling at zero upfront is trading at par."""

    @pytest.mark.parametrize("coupon", [0.01, 0.05, 0.0025])
    def test_a_zero_upfront_implies_a_spread_equal_to_the_coupon(self, coupon: float) -> None:
        spec = _spec(coupon=coupon)
        assert spread_from_upfront(spec, 0.0, DISCOUNT).value == pytest.approx(coupon, rel=1e-6)

    def test_a_buyer_paying_implies_a_spread_above_the_coupon(self) -> None:
        spec = _spec(coupon=0.05)
        assert spread_from_upfront(spec, 0.02 * NOTIONAL, DISCOUNT).value > 0.05

    def test_a_seller_paying_implies_a_spread_below_the_coupon(self) -> None:
        """The live 1Y: 180bp against a 500bp coupon, so the seller pays."""
        spec = _spec(coupon=0.05, years=1)
        assert spread_from_upfront(spec, -0.03 * NOTIONAL, DISCOUNT).value < 0.05


class TestMonotonicity:
    """Upfront is strictly increasing in the hazard, which is what makes
    bisection unconditionally convergent. If it ever stops holding, the
    solve is bracketing something it cannot bisect."""

    def test_a_larger_upfront_implies_a_wider_spread(self) -> None:
        spec = _spec()
        spreads = [
            spread_from_upfront(spec, points * NOTIONAL, DISCOUNT).value
            for points in (-0.02, 0.0, 0.02, 0.05, 0.10)
        ]
        assert spreads == sorted(spreads)

    @settings(max_examples=40, deadline=None)
    @given(st.floats(min_value=0.0, max_value=2.0))
    def test_upfront_rises_with_the_hazard(self, hazard: float) -> None:
        spec = _spec()
        lower = price_cds(spec, hazard, DISCOUNT).value.upfront
        higher = price_cds(spec, hazard + 0.01, DISCOUNT).value.upfront
        assert higher > lower


class TestWhatItRefuses:
    def test_an_unreachable_upfront_raises(self) -> None:
        """Beyond `(1 - recovery)` there is no hazard that gets there. A
        clamped answer at the bracket end would look like a very distressed
        name rather than like a contract these terms do not describe."""
        with pytest.raises(UpfrontOutOfRangeError):
            spread_from_upfront(_spec(), 0.95 * NOTIONAL, DISCOUNT)

    def test_a_seller_payment_larger_than_the_premium_leg_raises(self) -> None:
        with pytest.raises(UpfrontOutOfRangeError):
            spread_from_upfront(_spec(), -0.95 * NOTIONAL, DISCOUNT)

    def test_the_refusal_names_both_ends_of_the_band(self) -> None:
        """So a reader can see whether the input was wrong or the contract
        was unusual."""
        with pytest.raises(UpfrontOutOfRangeError, match="zero hazard"):
            spread_from_upfront(_spec(), 0.95 * NOTIONAL, DISCOUNT)

    def test_the_boundary_itself_is_solvable(self) -> None:
        """Proves the refusals turn on being outside the band rather than
        on the check being too eager."""
        spec = _spec()
        top = price_cds(spec, HAZARD_CEILING, DISCOUNT).value.upfront
        assert spread_from_upfront(spec, top * 0.999, DISCOUNT).value > 0


class TestItIsDeterministic:
    def test_the_same_inputs_give_the_same_answer(self) -> None:
        """A fixed step count rather than "until converged": the same
        property `parser_version` protects for ingest, applied to a solver."""
        spec = _spec()
        answers = {spread_from_upfront(spec, 0.02 * NOTIONAL, DISCOUNT).value for _ in range(5)}
        assert len(answers) == 1

    def test_the_step_count_is_far_past_double_precision(self) -> None:
        """A ceiling never reached, not a tolerance anyone tunes: 200
        halvings take a 5.0-wide bracket below 1e-59."""
        assert HAZARD_CEILING / 2**SOLVE_STEPS < 1e-50


class TestTheModelEnvelope:
    """I3: no analytic returns a bare number."""

    def test_the_spread_carries_its_model_id(self) -> None:
        result = spread_from_upfront(_spec(), 0.02 * NOTIONAL, DISCOUNT)
        assert result.model_id == "credit.spread_from_upfront"

    def test_the_hazard_carries_its_own(self) -> None:
        result = hazard_from_upfront(_spec(), 0.02 * NOTIONAL, DISCOUNT)
        assert result.model_id == "credit.hazard_from_upfront"

    def test_it_is_distinguishable_from_a_quoted_spread(self) -> None:
        """The point of the id travelling with it. A spread a counterparty
        agreed and one this model inferred under a 40% recovery are not the
        same claim, and a screen that showed them in one column would be
        stating the second as the first."""
        implied = spread_from_upfront(_spec(), 0.02 * NOTIONAL, DISCOUNT)
        priced = par_spread(_spec(), 0.05, DISCOUNT)
        assert implied.model_id != priced.model_id
