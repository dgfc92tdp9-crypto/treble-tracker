"""`ECO` — the macro dashboard.

Twenty-five FRED series were being ingested and refreshed daily with no
screen able to display any of them. That is the mirror of the defect this
repository named as its most common in Phase 2 — working analytics nothing
can display — and it had been sitting in the store the whole time.

The tests are mostly about the two columns that stop the table lying: the
unit, and the date the observation was made.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.macro import CATALOGUE, GROUPS, Frequency, macro_dashboard

KNOWN = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def _store(tmp_path: Path, rows: list[tuple[str, date, float | None]]) -> DuckStore:
    store = DuckStore(tmp_path / "m.db")
    record = Provenance(
        source_system="fred",
        source_uri="https://example.invalid/fred",
        retrieved_at=KNOWN,
        method=ExtractionMethod.BULK_FILE,
        extractor_version="1",
        payload_hash="a" * 64,
    )
    store.write_provenance([record])
    store.write_facts(
        [
            Fact(
                subject=f"fred:{series}",
                field="PX_LAST",
                value=value,
                effective_from=when,
                effective_to=when,
                knowledge_from=KNOWN,
                provenance_id=record.id,
            )
            for series, when, value in rows
        ]
    )
    return store


class TestTheCatalogueIsTheOnlyPlaceUnitsLive:
    def test_every_series_declares_a_unit_and_a_group(self) -> None:
        """A row without a unit is a number a reader will guess at, and
        CPIAUCSL at 332.568 guesses badly."""
        for series in CATALOGUE:
            assert series.unit, series.series_id
            assert series.group in GROUPS, series.series_id

    def test_series_ids_are_unique(self) -> None:
        """A duplicate would render twice and be read as corroboration."""
        ids = [s.series_id for s in CATALOGUE]
        assert len(ids) == len(set(ids))

    def test_an_index_and_a_percentage_are_not_given_the_same_unit(self) -> None:
        """The specific confusion this column exists to prevent."""
        by_id = {s.series_id: s for s in CATALOGUE}
        assert by_id["UNRATE"].unit == "%"
        assert "index" in by_id["CPIAUCSL"].unit


class TestTheReadings:
    def test_the_latest_observation_wins(self, tmp_path: Path) -> None:
        store = _store(
            tmp_path,
            [("VIXCLS", date(2026, 8, 5), 15.5), ("VIXCLS", date(2026, 8, 7), 14.9)],
        )
        vix = next(r for r in macro_dashboard(store, as_of=LATER) if r.series.series_id == "VIXCLS")
        assert vix.value == pytest.approx(14.9)
        assert vix.observed == date(2026, 8, 7)

    def test_the_change_is_against_the_previous_observation(self, tmp_path: Path) -> None:
        store = _store(
            tmp_path,
            [("VIXCLS", date(2026, 8, 5), 15.5), ("VIXCLS", date(2026, 8, 7), 14.9)],
        )
        vix = next(r for r in macro_dashboard(store, as_of=LATER) if r.series.series_id == "VIXCLS")
        assert vix.change == pytest.approx(-0.6)

    def test_a_single_observation_has_no_change(self, tmp_path: Path) -> None:
        """Not zero. A series that has printed once has no change to
        report, and zero would read as "unmoved"."""
        store = _store(tmp_path, [("VIXCLS", date(2026, 8, 7), 14.9)])
        vix = next(r for r in macro_dashboard(store, as_of=LATER) if r.series.series_id == "VIXCLS")
        assert vix.change is None

    def test_a_missing_value_is_skipped_not_read_as_zero(self, tmp_path: Path) -> None:
        """FRED writes '.' for a day a series does not publish, which lands
        as a null. Treated as a value it would be a VIX of zero — and a
        change column would report the whole level as the move."""
        store = _store(
            tmp_path,
            [
                ("VIXCLS", date(2026, 8, 5), 15.5),
                ("VIXCLS", date(2026, 8, 6), 14.9),
                ("VIXCLS", date(2026, 8, 7), None),
            ],
        )
        vix = next(r for r in macro_dashboard(store, as_of=LATER) if r.series.series_id == "VIXCLS")
        assert vix.value == pytest.approx(14.9)
        assert vix.observed == date(2026, 8, 6)

    def test_it_is_point_in_time(self, tmp_path: Path) -> None:
        """I2. Asking what the dashboard showed before anything was known
        must not return today's numbers."""
        store = _store(tmp_path, [("VIXCLS", date(2026, 8, 7), 14.9)])
        early = datetime(2026, 7, 1, tzinfo=UTC)
        vix = next(r for r in macro_dashboard(store, as_of=early) if r.series.series_id == "VIXCLS")
        assert vix.value is None
        assert vix.ingested is False


class TestAbsenceIsDistinguished:
    def test_a_series_never_ingested_is_reported_not_dropped(self, tmp_path: Path) -> None:
        """A configuration gap — nobody ever fetched this series — and a
        series with no print today look identical if the row is dropped,
        and only one is worth acting on."""
        readings = macro_dashboard(_store(tmp_path, []), as_of=LATER)
        assert len(readings) == len(CATALOGUE)
        assert all(not r.ingested for r in readings)
        assert all(r.staleness(today=LATER.date()) == "not ingested" for r in readings)


class TestStalenessIsJudgedPerSeries:
    def test_a_monthly_series_six_weeks_old_is_not_stale(self, tmp_path: Path) -> None:
        """The case a single threshold gets wrong. CPI is released about a
        fortnight after the month it covers, so a June print in mid-August
        is routine — and a warning that fires every month stops being
        read."""
        store = _store(tmp_path, [("CPIAUCSL", date(2026, 6, 1), 332.568)])
        cpi = next(
            r for r in macro_dashboard(store, as_of=LATER) if r.series.series_id == "CPIAUCSL"
        )
        assert cpi.staleness(today=LATER.date()) == ""

    def test_a_daily_series_six_weeks_old_is_stale(self, tmp_path: Path) -> None:
        """Same age, different verdict — which is the whole point of
        judging against the series' own frequency."""
        store = _store(tmp_path, [("VIXCLS", date(2026, 6, 1), 14.9)])
        vix = next(r for r in macro_dashboard(store, as_of=LATER) if r.series.series_id == "VIXCLS")
        assert "stale" in vix.staleness(today=LATER.date())
        assert "daily" in vix.staleness(today=LATER.date())

    def test_every_frequency_tolerates_at_least_its_own_period(self) -> None:
        """A tolerance shorter than the publication interval would mark a
        series stale the day before its next release, every time."""
        assert Frequency.DAILY.tolerated_days >= 1
        assert Frequency.WEEKLY.tolerated_days >= 7
        assert Frequency.MONTHLY.tolerated_days >= 31
        assert Frequency.QUARTERLY.tolerated_days >= 92


class TestTheBinding:
    @staticmethod
    def _rows(store: DuckStore, binding: str) -> tuple[tuple[object, ...], ...]:
        from treble.tapi.local import LocalTapi

        return LocalTapi(store).series(None, binding, as_of=LATER)

    def test_the_dashboard_carries_unit_and_date_on_every_row(self, tmp_path: Path) -> None:
        rows = self._rows(
            _store(tmp_path, [("VIXCLS", date(2026, 8, 7), 14.9)]), "sys:eco_dashboard"
        )
        assert len(rows) == len(CATALOGUE)
        vix = next(r for r in rows if r[1] == "VIXCLS")
        assert vix[3] == "index"
        assert vix[5] == "2026-08-07"

    def test_the_change_is_rounded_in_the_binding(self, tmp_path: Path) -> None:
        """A change of -0.029999999999999805 is float noise from the
        subtraction and would show in every surface that displayed it.
        Rounded once here so the two renderers agree."""
        store = _store(
            tmp_path, [("SOFR", date(2026, 8, 6), 3.65), ("SOFR", date(2026, 8, 7), 3.62)]
        )
        sofr = next(r for r in self._rows(store, "sys:eco_dashboard") if r[1] == "SOFR")
        assert sofr[6] == pytest.approx(-0.03)
        assert str(sofr[6]) == "-0.03"

    def test_the_method_tab_states_the_staleness_rule(self, tmp_path: Path) -> None:
        rows = dict(self._rows(_store(tmp_path, []), "sys:eco_method"))  # type: ignore[arg-type]
        assert "FRED" in str(rows["Source"])
        assert "daily" in str(rows["Stale after"])
        assert "revision" in str(rows["Change column"])
