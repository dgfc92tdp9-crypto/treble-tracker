"""`CDSW` against ISDA's published test grids (Phase 2 gate criterion).

    This application is based on the ISDA CDS Standard Model.
    Copyright © ISDA and S&P Global. All rights reserved.

The criterion is *external* validation, and the distinction is the whole
point of it. `test_cds.py` pins the model's internal relationships — a par
coupon settles at zero, the credit triangle is explicitly not exact — and
every one of those would still pass if the conventions were wrong in some
self-consistent way. These do not: the expected upfronts are ISDA's,
computed by the reference implementation the market settles against.

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
TRADE_DATE = date(2022, 6, 22)
NOTIONAL = 1e7

#: Error in basis points of notional that the shipped model may leave against
#: ISDA's own numbers. Not a round number chosen for comfort — it is the
#: measured residual plus headroom, and `test_the_residual_is_discretisation`
#: below establishes *what* it is, so a future regression that changes the
#: conventions cannot hide inside it.
MEDIAN_TOLERANCE_BP = 0.40
WORST_TOLERANCE_BP = 2.00


def _curve() -> Curve:
    raw = [
        (row["tenor"], float(row["rate"]))
        for row in csv.DictReader((FIXTURES / "usd_sofr_20220622_curve.csv").open())
    ]
    config = CurveConfig(
        name="ISDA-USD-SOFR-20220622",
        currency="USD",
        calendar=Market.US_SETTLEMENT,
        day_count=DayCount.ACT_365F,
        settlement_days=0,
        instruments=tuple(
            InstrumentSpec(kind=InstrumentKind.SWAP, tenor=tenor) for tenor, _ in raw
        ),
    )
    return build_curve(
        config, {(InstrumentKind.SWAP, tenor): rate for tenor, rate in raw}, as_of=TRADE_DATE
    )


def _cases() -> list[dict[str, str]]:
    return list(csv.DictReader((FIXTURES / "usd_sofr_20220622_grid.csv").open()))


def _errors_bp(curve: Curve) -> list[tuple[int, float]]:
    """(maturity in years, signed error in bp of notional) per published case.

    The hazard is calibrated so that *our* par spread equals ISDA's quoted
    spread, then the trade is priced at its own coupon. That is the ISDA
    workflow, and it is what makes this a test of the pricing conventions
    rather than of a curve-fitting coincidence.
    """
    out: list[tuple[int, float]] = []
    for row in _cases():
        maturity = date.fromisoformat(row["maturity_date"])
        spec = CdsSpec(
            notional=NOTIONAL,
            coupon=float(row["coupon_bp"]) / 1e4,
            trade_date=TRADE_DATE,
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
                round((maturity - TRADE_DATE).days / 365.25),
                (ours - float(row["clean_upfront"])) * 1e4,
            )
        )
    return out


@pytest.fixture(scope="module")
def errors() -> list[tuple[int, float]]:
    return _errors_bp(_curve())


@pytest.mark.golden
class TestAgainstISDAsOwnNumbers:
    def test_the_grid_is_substantial(self) -> None:
        """Guards the checks below: a fixture that had lost its rows would
        pass every tolerance by having nothing to compare."""
        cases = _cases()
        assert len(cases) > 2000
        assert {c["currency"].strip() for c in cases} == {"USD"}

    def test_the_model_reproduces_isda_upfronts(self, errors: list[tuple[int, float]]) -> None:
        magnitudes = [abs(e) for _, e in errors]
        median = statistics.median(magnitudes)
        assert median < MEDIAN_TOLERANCE_BP, f"median error {median:.4f}bp"

    def test_no_single_case_is_far_out(self, errors: list[tuple[int, float]]) -> None:
        """A good median with one wild case would mean a convention that is
        right on average and wrong somewhere specific."""
        worst = max(abs(e) for _, e in errors)
        assert worst < WORST_TOLERANCE_BP, f"worst error {worst:.4f}bp"

    def test_the_error_does_not_favour_one_direction(self, errors: list[tuple[int, float]]) -> None:
        """A signed bias would be a convention error; unbiased scatter is
        discretisation. This is the check that tells the two apart."""
        signed = [e for _, e in errors]
        assert abs(statistics.mean(signed)) < MEDIAN_TOLERANCE_BP


@pytest.mark.golden
class TestWhatTheResidualIs:
    """Attributing the remaining error, so the tolerance is not a hiding place."""

    def test_the_residual_is_discretisation(self, errors: list[tuple[int, float]]) -> None:
        """It grows with maturity, which is what a per-period approximation
        does and what a wrong day count or sign would not.

        `price_cds` discounts protection to each period's midpoint and
        accrues default at the midpoint too. Both are exact only in the
        limit of short periods, so the error accumulates with the number of
        periods. Measured: ~0.11bp at one year against ~0.52bp at ten.
        Removing it needs real IMM payment schedules and a finer protection
        integral rather than a different convention.
        """
        by_maturity: dict[int, list[float]] = {}
        for years, error in errors:
            by_maturity.setdefault(years, []).append(abs(error))
        medians = {y: statistics.median(v) for y, v in sorted(by_maturity.items())}
        assert medians[1] < medians[10], f"error did not grow with maturity: {medians}"
        # And the short end is close enough that the conventions themselves
        # cannot be wrong.
        assert medians[1] < 0.15, (
            f"one-year error {medians[1]:.4f}bp is too large to be discretisation"
        )
