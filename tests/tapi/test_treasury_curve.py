"""The CMT curve binding behind ICVS.

A yield curve is the easiest thing in this system to render plausibly and
wrongly: every failure mode still produces a smooth upward-sloping line. So
the properties asserted here are the ones a reader cannot check by looking.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.local import LocalTapi

AS_OF = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


def _provenance() -> Provenance:
    return Provenance(
        source_system="fred",
        source_uri="https://fred.stlouisfed.org/graph/fredgraph.csv",
        retrieved_at=AS_OF,
        method=ExtractionMethod.BULK_FILE,
        extractor_version="1",
    )


@pytest.fixture
def tapi(tmp_path: Path) -> LocalTapi:
    store = DuckStore(tmp_path / "curve.db")
    record = _provenance()
    store.write_provenance([record])
    points = {"DGS1MO": 3.80, "DGS2": 4.33, "DGS10": 4.69, "DGS30": 5.16}
    store.write_facts(
        [
            Fact(
                subject=f"fred:{series}",
                field="PX_LAST",
                value=rate,
                effective_from=date(2026, 7, 24),
                knowledge_from=datetime(2026, 7, 25, tzinfo=UTC),
                provenance_id=record.id,
            )
            for series, rate in points.items()
        ]
    )
    return LocalTapi(store)


class TestCurveShape:
    def test_points_come_back_in_tenor_order(self, tapi: LocalTapi) -> None:
        """ "DGS1MO" sorts after "DGS10" alphabetically. A curve in
        alphabetical order is not a curve, and it still looks like one."""
        rows = tapi.series(None, "sys:treasury_curve", as_of=AS_OF)
        years = [row[1] for row in rows]
        assert years == sorted(years)  # type: ignore[type-var]
        assert [row[0] for row in rows] == ["1M", "2Y", "10Y", "30Y"]

    def test_absent_tenors_are_omitted_not_interpolated(self, tapi: LocalTapi) -> None:
        """Only four tenors were written; the other seven must not appear.
        An invented point on a curve is indistinguishable from an observed
        one once it is drawn."""
        rows = tapi.series(None, "sys:treasury_curve", as_of=AS_OF)
        assert len(rows) == 4

    def test_each_point_carries_its_observation_date(self, tapi: LocalTapi) -> None:
        rows = tapi.series(None, "sys:treasury_curve", as_of=AS_OF)
        assert all(row[3] == "2026-07-24" for row in rows)

    def test_rates_are_the_stored_values(self, tapi: LocalTapi) -> None:
        rows = tapi.series(None, "sys:treasury_curve", as_of=AS_OF)
        assert dict(zip([r[0] for r in rows], [r[2] for r in rows], strict=True)) == {
            "1M": 3.80,
            "2Y": 4.33,
            "10Y": 4.69,
            "30Y": 5.16,
        }


class TestPointInTime:
    def test_knowledge_after_as_of_is_invisible(self, tapi: LocalTapi, tmp_path: Path) -> None:
        """I2 holds for the curve as for everything else."""
        before = datetime(2026, 7, 24, tzinfo=UTC)  # before knowledge_from
        assert tapi.series(None, "sys:treasury_curve", as_of=before) == ()

    def test_empty_store_yields_an_empty_curve(self, tmp_path: Path) -> None:
        """Not a zero curve, not a flat line: nothing."""
        empty = LocalTapi(DuckStore(tmp_path / "empty.db"))
        assert empty.series(None, "sys:treasury_curve", as_of=AS_OF) == ()


def test_tenor_table_is_ordered_and_consistent() -> None:
    """The tenor table itself, since every ordering guarantee rests on it."""
    years = [entry[2] for entry in LocalTapi.CMT_TENORS]
    assert years == sorted(years)
    assert len({entry[0] for entry in LocalTapi.CMT_TENORS}) == len(LocalTapi.CMT_TENORS)
