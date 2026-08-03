"""`CDSW` against ISDA's published test grids (Phase 2 gate criterion).

    This application is based on the ISDA CDS Standard Model.
    Copyright © ISDA and S&P Global. All rights reserved.

The criterion is *external* validation, and the distinction is the whole
point of it. `test_cds.py` pins the model's internal relationships — a par
coupon settles at zero, the credit triangle is explicitly not exact — and
every one of those would still pass if the conventions were wrong in some
self-consistent way. These do not: the expected upfronts are ISDA's,
computed by the reference implementation the market settles against.

Six currencies, because one currency cannot separate a convention error from
a curve-level one. The five 2021-04-30 grids are traded 41 days into a
coupon period; USD 2022-06-22 is traded 2 days after a roll. That contrast
is what `TestWhatTheResidualIs` below reads.

See `tests/fixtures/isda/README.md` for provenance and licence.
"""

from __future__ import annotations

import csv
import statistics
from datetime import date
from pathlib import Path

import pytest
from scipy.optimize import brentq

from treble.analytics._ql import DayCount, Market
from treble.analytics.credit.cds import CdsSpec, par_spread, price_cds
from treble.analytics.curves.bootstrap import Curve, build_curve
from treble.analytics.curves.config import CurveConfig, InstrumentKind, InstrumentSpec

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "isda"
NOTIONAL = 1e7

#: The published grids, with the stride applied to each. Every grid holds
#: 2,388 cases and costs ~14s to evaluate in full, so the five added
#: currencies are thinned; USD stays whole because it was validated whole and
#: thinning it would quietly weaken a check that already passes.
#: `test_the_sample_keeps_the_hardest_case` stops the stride from becoming a
#: way for the worst-case tolerance to pass by omission.
GRIDS: dict[str, int] = {
    "usd_sofr_20220622": 1,
    "aud_aonia_20210430": 3,
    "chf_saron_20210430": 3,
    "eur_ester_20210430": 3,
    "gbp_sonia_20210430": 3,
    "jpy_tona_20210430": 3,
}

#: Error in basis points of notional that the shipped model may leave against
#: ISDA's own numbers. Measured worsts, full grids:
#:
#:     AUD 6.5125   GBP 3.0584   CHF 1.7590
#:     EUR 1.4404   USD 1.4185   JPY 0.4659
#:
#: and medians from 0.0424 (JPY) to 0.3259 (USD). The worst tolerance is wide
#: because a single shape drives every grid's maximum — see
#: `TestWhatTheResidualIs` — so it is fenced by an assertion that the maximum
#: *is* that shape, rather than left as room for a new regression to hide in.
MEDIAN_TOLERANCE_BP = 0.40
WORST_TOLERANCE_BP = 8.00


def _cases(stem: str) -> list[dict[str, str]]:
    rows = list(csv.DictReader((FIXTURES / f"{stem}_grid.csv").open()))
    return rows[:: GRIDS[stem]]


def _curve(stem: str) -> Curve:
    raw = [
        (row["tenor"], float(row["rate"]))
        for row in csv.DictReader((FIXTURES / f"{stem}_curve.csv").open())
    ]
    rows = _cases(stem)
    config = CurveConfig(
        name=f"ISDA-{stem.upper()}",
        currency=rows[0]["currency"].strip(),
        calendar=Market.US_SETTLEMENT,
        day_count=DayCount.ACT_365F,
        settlement_days=0,
        instruments=tuple(
            InstrumentSpec(kind=InstrumentKind.SWAP, tenor=tenor) for tenor, _ in raw
        ),
    )
    return build_curve(
        config,
        {(InstrumentKind.SWAP, tenor): rate for tenor, rate in raw},
        as_of=date.fromisoformat(rows[0]["trade_date"]),
    )


def _errors_bp(stem: str) -> list[tuple[int, float]]:
    """(maturity in years, signed error in bp of notional) per published case.

    The hazard is calibrated so that *our* par spread equals ISDA's quoted
    spread, then the trade is priced at its own coupon. That is the ISDA
    workflow, and it is what makes this a test of the pricing conventions
    rather than of a curve-fitting coincidence.
    """
    curve = _curve(stem)
    rows = _cases(stem)
    trade_date = date.fromisoformat(rows[0]["trade_date"])
    out: list[tuple[int, float]] = []
    for row in rows:
        maturity = date.fromisoformat(row["maturity_date"])
        spec = CdsSpec(
            notional=NOTIONAL,
            coupon=float(row["coupon_bp"]) / 1e4,
            trade_date=trade_date,
            maturity=maturity,
            recovery=float(row["recovery"]),
        )
        quoted = float(row["quoted_spread_bp"]) / 1e4
        hazard = brentq(
            lambda h, s=spec, q=quoted: par_spread.__wrapped__(s, h, curve) - q,
            1e-12,
            10.0,
            xtol=1e-15,
        )
        ours = price_cds.__wrapped__(spec, hazard, curve).upfront / NOTIONAL
        out.append(
            (
                round((maturity - trade_date).days / 365.25),
                (ours - float(row["clean_upfront"])) * 1e4,
            )
        )
    return out


@pytest.fixture(scope="module", params=sorted(GRIDS))
def grid(request: pytest.FixtureRequest) -> tuple[str, list[tuple[int, float]]]:
    stem: str = request.param
    return stem, _errors_bp(stem)


@pytest.mark.golden
class TestAgainstISDAsOwnNumbers:
    def test_the_grid_is_substantial(self, grid: tuple[str, list[tuple[int, float]]]) -> None:
        """Guards the checks below: a fixture that had lost its rows would
        pass every tolerance by having nothing to compare."""
        stem, errors = grid
        assert len(errors) > 700, f"{stem}: only {len(errors)} cases"
        assert len({case["currency"].strip() for case in _cases(stem)}) == 1

    def test_the_sample_keeps_the_hardest_case(
        self, grid: tuple[str, list[tuple[int, float]]]
    ) -> None:
        """The stride must not drop the shape that drives the maximum.

        Without this, thinning a grid would relax `test_no_single_case_is_far_out`
        silently: the tolerance would still read 8bp and the case that needs
        that much room would simply not be evaluated.
        """
        stem, _ = grid
        rows = _cases(stem)
        longest = max(date.fromisoformat(row["maturity_date"]) for row in rows)
        hardest = [
            row
            for row in rows
            if date.fromisoformat(row["maturity_date"]) == longest
            and float(row["coupon_bp"]) >= 500
            and float(row["quoted_spread_bp"]) <= 25
        ]
        assert hardest, f"{stem}: the stride dropped the long-dated high-coupon cases"

    def test_the_model_reproduces_isda_upfronts(
        self, grid: tuple[str, list[tuple[int, float]]]
    ) -> None:
        stem, errors = grid
        median = statistics.median([abs(error) for _, error in errors])
        assert median < MEDIAN_TOLERANCE_BP, f"{stem}: median error {median:.4f}bp"

    def test_no_single_case_is_far_out(self, grid: tuple[str, list[tuple[int, float]]]) -> None:
        """A good median with one wild case would mean a convention that is
        right on average and wrong somewhere specific."""
        stem, errors = grid
        worst = max(abs(error) for _, error in errors)
        assert worst < WORST_TOLERANCE_BP, f"{stem}: worst error {worst:.4f}bp"

    def test_the_error_does_not_favour_one_direction(
        self, grid: tuple[str, list[tuple[int, float]]]
    ) -> None:
        """A large signed bias would be a convention error. The residual is
        mildly negative in every currency (-0.04bp to -0.26bp), an order of
        magnitude below the spread of the errors themselves."""
        stem, errors = grid
        signed = statistics.mean([error for _, error in errors])
        assert abs(signed) < MEDIAN_TOLERANCE_BP, f"{stem}: signed mean {signed:+.4f}bp"


@pytest.mark.golden
class TestWhatTheResidualIs:
    """Attributing the remaining error, so the tolerance is not a hiding place.

    Two candidate causes have been tested and **refuted**. They are recorded
    here so that neither is attempted a second time:

    1. *Protection-integral discretisation.* `price_cds` discounts protection
       to each period's midpoint. Sub-dividing that integral 2, 4, 8 and 16
       times moves the worst AUD case from 6.5125bp to 6.5127bp — no effect
       at any resolution.
    2. *The uniform payment schedule.* `price_cds` spreads periods evenly
       rather than placing them on 20 Mar/Jun/Sep/Dec rolls. Replacing it
       with the real IMM schedule, first accrual clipped to step-in, made
       every grid **worse**: AUD median 0.060bp to 1.087bp, USD 0.326bp to
       1.188bp.

    What is left is a lead, not a conclusion, and the tests below assert only
    what is measured. The lead: the five grids traded 41 days into a coupon
    period carry the larger maxima, and within that group the ordering tracks
    the currency's rate level (AUD 1.51%, worst at 6.51bp; JPY 0.08%, best at
    0.47bp) — while USD, the highest rate of all at 3.09%, sits near the
    bottom at 1.42bp on a 2-day stub. Front-stub handling is the thing to
    examine next, and refutation (2) says it is not as simple as generating
    the right dates.
    """

    def test_the_error_grows_with_maturity(self, grid: tuple[str, list[tuple[int, float]]]) -> None:
        """A per-period approximation accumulates; a wrong day count or a
        sign error would not. This is the check that tells those apart."""
        stem, errors = grid
        by_maturity: dict[int, list[float]] = {}
        for years, error in errors:
            by_maturity.setdefault(years, []).append(abs(error))
        medians = {years: statistics.median(v) for years, v in sorted(by_maturity.items())}
        assert medians[1] < medians[10], f"{stem}: error did not grow with maturity: {medians}"

    def test_the_short_end_exonerates_the_conventions(
        self, grid: tuple[str, list[tuple[int, float]]]
    ) -> None:
        """At one year there are four periods, so a per-period approximation
        has barely accumulated. A large error here would implicate the
        conventions themselves — day count, accrual basis, recovery."""
        stem, errors = grid
        one_year = [abs(error) for years, error in errors if years == 1]
        assert one_year, f"{stem}: no one-year cases"
        median = statistics.median(one_year)
        assert median < 0.15, f"{stem}: one-year error {median:.4f}bp implicates the conventions"

    def test_the_worst_case_is_the_known_shape(
        self, grid: tuple[str, list[tuple[int, float]]]
    ) -> None:
        """Every grid's maximum sits at its longest maturity. That is what
        fences `WORST_TOLERANCE_BP`: the tolerance is wide enough to admit
        AUD's 6.51bp, and this asserts the case spending that room is the
        long-dated one rather than something new."""
        stem, errors = grid
        longest = max(years for years, _ in errors)
        worst_years = max(errors, key=lambda pair: abs(pair[1]))[0]
        assert worst_years == longest, (
            f"{stem}: worst case sits at {worst_years}y, not the longest ({longest}y) — "
            "the residual changed shape and the tolerance no longer bounds what it was set for"
        )
