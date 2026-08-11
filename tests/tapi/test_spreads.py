"""The government curve `SPRD` measures against.

Built from the CMT par yields the Treasury adapter ingests. The tests are
about the two decisions that make it a curve rather than a set of numbers:
which tenors may be treated as par bonds, and which day to build on.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.provenance import ExtractionMethod, Provenance
from treble.ingest.treasury_curve import CURVE, FIELD
from treble.store.duck import DuckStore
from treble.tapi.spreads import (
    MIN_GOVT_NODES,
    GovtCurveUnavailableError,
    build_govt_curve,
    govt_curve_dates,
)

KNOWN = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
DAY = date(2026, 8, 7)
PAR = {"1Y": 0.0401, "2Y": 0.0419, "3Y": 0.0425, "5Y": 0.0435, "7Y": 0.0449, "10Y": 0.0465}


def _store(
    tmp_path: Path, points: dict[str, float], *, when: date = DAY, name: str = "g"
) -> DuckStore:
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = DuckStore(tmp_path / f"{name}.db")
    record = Provenance(
        source_system="treasury-curve",
        source_uri="https://example.invalid/ust",
        retrieved_at=KNOWN,
        method=ExtractionMethod.BULK_FILE,
        extractor_version="1",
        payload_hash="a" * 64,
    )
    store.write_provenance([record])
    store.write_facts(
        [
            Fact(
                subject=f"govt:{CURVE}:{tenor}",
                field=FIELD,
                value=rate,
                effective_from=when,
                effective_to=when,
                knowledge_from=KNOWN,
                provenance_id=record.id,
            )
            for tenor, rate in points.items()
        ]
    )
    return store


class TestItBuilds:
    def test_a_full_curve_bootstraps(self, tmp_path: Path) -> None:
        curve, day = build_govt_curve(_store(tmp_path, PAR), as_of=LATER)
        assert day == DAY
        assert 0.03 < curve.zero(10.0) < 0.06

    def test_the_curve_slopes_the_way_its_inputs_do(self, tmp_path: Path) -> None:
        """An upward-sloping par curve must not bootstrap to an inverted
        zero curve. A shape test catches a sign or ordering error that a
        single-point assertion would sail past."""
        curve, _ = build_govt_curve(_store(tmp_path, PAR), as_of=LATER)
        assert curve.zero(1.0) < curve.zero(5.0) < curve.zero(10.0)

    def test_bills_are_excluded_rather_than_treated_as_par_bonds(self, tmp_path: Path) -> None:
        """Treasury quotes tenors under a year on a discount basis.
        Bootstrapping them as par bonds would misprice the short end, which
        is exactly where a two-year corporate reads its G-spread."""
        with_bills = dict(PAR)
        with_bills.update({"1M": 0.0379, "3M": 0.0387, "6M": 0.0396})
        curve, _ = build_govt_curve(_store(tmp_path, with_bills), as_of=LATER)
        bills_only, _ = build_govt_curve(_store(tmp_path, PAR, name="b"), as_of=LATER)
        assert curve.zero(10.0) == pytest.approx(bills_only.zero(10.0), abs=1e-9)


class TestItChoosesTheDayCarefully:
    def test_a_day_with_too_few_points_is_not_offered(self, tmp_path: Path) -> None:
        thin = {"1Y": 0.04, "2Y": 0.042}
        assert govt_curve_dates(_store(tmp_path, thin), as_of=LATER) == []

    def test_too_few_points_anywhere_is_an_error_that_says_why(self, tmp_path: Path) -> None:
        """Naming the bill exclusion in the message matters: a store full
        of short tenors looks well populated, and the reason it cannot
        build a curve is not obvious from the outside."""
        with pytest.raises(GovtCurveUnavailableError, match="discount basis"):
            build_govt_curve(_store(tmp_path, {"1M": 0.0379, "3M": 0.0387}), as_of=LATER)

    def test_it_prefers_the_newest_day_that_actually_builds(self, tmp_path: Path) -> None:
        """Newest *usable*, not newest. A thin latest day emptied SWPM's
        basis tab and DDIS's ladder before this rule was applied to them."""
        store = _store(tmp_path, PAR)
        assert build_govt_curve(store, as_of=LATER)[1] == DAY

    def test_an_explicit_date_with_no_curve_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(GovtCurveUnavailableError):
            build_govt_curve(_store(tmp_path, PAR), as_of=LATER, report_date=date(2020, 1, 1))

    def test_the_node_floor_is_what_it_claims(self, tmp_path: Path) -> None:
        """A curve with four points has no shape worth interpolating."""
        exact = dict(list(PAR.items())[:MIN_GOVT_NODES])
        assert govt_curve_dates(_store(tmp_path, exact), as_of=LATER) == [DAY]
        one_short = dict(list(PAR.items())[: MIN_GOVT_NODES - 1])
        assert govt_curve_dates(_store(tmp_path, one_short, name="s"), as_of=LATER) == []


class TestItIsPointInTime:
    def test_a_curve_is_not_visible_before_it_was_known(self, tmp_path: Path) -> None:
        """I2. Asking for the curve in July must not return something
        ingested in August."""
        store = _store(tmp_path, PAR)
        assert govt_curve_dates(store, as_of=datetime(2026, 7, 1, tzinfo=UTC)) == []
