"""How often a source publishes, and how often we pull it, are two things.

They coincide while everything is fetched as fast as it appears, and one
field served both. `gleif-isin` broke that: GLEIF republishes the ISIN-to-LEI
mapping daily, and it is a 26.6 MB full file with no delta feed — 9.72 GB a
year against 10 GB of free disk. The decision was to pull it weekly.

Recording that by setting `expected_cadence_days=7.0` would have written a
falsehood about GLEIF into the source's own description, and contradicted the
comment sitting directly above it. So the decision lives in
`fetch_cadence_days` and the fact stays where it was.

These tests exist because the two fields are easy to conflate again: a
consumer reading `expected_cadence_days` directly still compiles, still
passes its own tests, and quietly schedules a source seven times too often.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.ingest.test_growth import _log, _record, _store_payload
from treble.ingest.base import SourceMeta
from treble.ingest.growth import project
from treble.ingest.health import Freshness, source_health
from treble.ingest.registry import all_sources
from treble.store.ingest_log import IngestLog


def _meta(**kwargs: object) -> SourceMeta:
    return SourceMeta(source_id="x", description="d", licence="l", **kwargs)  # type: ignore[arg-type]


class TestEffectiveCadence:
    def test_the_fetch_cadence_wins_when_set(self) -> None:
        meta = _meta(expected_cadence_days=1.0, fetch_cadence_days=7.0)
        assert meta.effective_cadence_days == 7.0

    def test_it_falls_back_to_how_often_the_source_publishes(self) -> None:
        """The common case, and why nothing else had to change: a source
        with no separate policy is still scheduled by its publication rate."""
        assert _meta(expected_cadence_days=1.0).effective_cadence_days == 1.0

    def test_an_irregular_source_stays_irregular(self) -> None:
        assert _meta().effective_cadence_days is None

    def test_a_fetch_cadence_can_be_set_on_an_irregular_source(self) -> None:
        """A source with no publication rhythm can still be pulled on one."""
        assert _meta(fetch_cadence_days=30.0).effective_cadence_days == 30.0


class TestGleifIsin:
    """The change itself, asserted on the shipped metadata."""

    def _meta(self) -> SourceMeta:
        return all_sources()["gleif-isin"]

    def test_it_is_pulled_weekly(self) -> None:
        assert self._meta().fetch_cadence_days == 7.0

    def test_gleif_is_still_described_as_publishing_daily(self) -> None:
        """The fact the decision must not overwrite. If this ever reads 7.0,
        someone recorded a schedule by editing a description of GLEIF."""
        assert self._meta().expected_cadence_days == 1.0

    def test_the_schedule_is_the_weekly_one(self) -> None:
        assert self._meta().effective_cadence_days == 7.0


class TestHealthJudgesAgainstTheFetchCadence:
    """Otherwise a source deliberately pulled weekly reports overdue every
    week — the "health check nothing can ever satisfy" that `refresh`'s own
    docstring warns about, which teaches a reader to ignore the column.
    """

    def _health(self, tmp_path: Path, age_days: float) -> Freshness:
        now = datetime(2026, 9, 8, tzinfo=UTC)
        log = IngestLog(tmp_path / "l.db")
        log.append(
            source="gleif-isin",
            payload_hash="0" * 64,
            source_uri="https://example.invalid/isin",
            fetched_at=now - timedelta(days=age_days),
            parser_version="1",
        )
        states = {s.source_id: s for s in source_health(log, now=now)}
        return states["gleif-isin"].freshness

    def test_six_days_is_fresh(self, tmp_path: Path) -> None:
        assert self._health(tmp_path, 6) is Freshness.FRESH

    def test_ten_days_is_still_within_tolerance(self, tmp_path: Path) -> None:
        """Weekly cadence, doubled plus a day: 15."""
        assert self._health(tmp_path, 10) is Freshness.FRESH

    def test_twenty_days_is_overdue(self, tmp_path: Path) -> None:
        assert self._health(tmp_path, 20) is Freshness.OVERDUE

    def test_six_days_would_have_been_overdue_under_the_daily_cadence(self, tmp_path: Path) -> None:
        """Proves the first assertion turns on the new field rather than on
        six days being comfortable anyway. A one-day cadence tolerates
        three; six would have been overdue, which is the state this source
        was permanently in."""
        from treble.ingest.health import _tolerance

        assert _tolerance(1.0) < 6
        assert _tolerance(7.0) >= 6


class TestGrowthProjectsAgainstTheFetchCadence:
    def test_a_weekly_pull_costs_a_seventh_of_a_daily_one(self, tmp_path: Path) -> None:
        """The point of the change. Projecting against the publication rate
        would keep charging the disk for six downloads a week that are not
        going to happen."""
        import os

        payloads = tmp_path / "payloads"
        log = _log(tmp_path)
        # `fred` publishes daily and has no separate fetch cadence;
        # `gleif-isin` publishes daily and is pulled weekly. Same payload
        # size, so any difference is the cadence.
        body = os.urandom(70_000)
        _record(log, "fred", _store_payload(payloads, body), 1)
        _record(log, "gleif-isin", _store_payload(payloads, body + b"!"), 1)

        rates = dict(project(log, payloads).contributors)
        assert (
            rates["fred"] == 7 * rates["gleif-isin"]
            or abs(rates["fred"] / rates["gleif-isin"] - 7) < 0.01
        )
