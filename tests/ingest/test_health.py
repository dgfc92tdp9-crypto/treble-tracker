"""Source health: whether the data is still flowing.

The report exists because `treble status` counted payloads, and a count can
only go up. A source that stopped publishing, changed its URL, or had its
free tier withdrawn rendered exactly like a healthy one — the number simply
stopped changing, on a screen nobody diffed against last week's.

Every test here is about the report being able to say something bad. A
health check that cannot report ill health is the same object as a test
that cannot fail, and it would be worse than nothing: it would be
reassuring.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from treble.ingest.health import (
    Freshness,
    overdue,
    source_health,
)
from treble.store.ingest_log import IngestLog

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


@pytest.fixture
def log(tmp_path: Path) -> IngestLog:
    return IngestLog(tmp_path / "l.db")


def _append(log: IngestLog, source: str, *, days_ago: float) -> None:
    log.append(
        source=source,
        payload_hash="a" * 64,
        source_uri="https://example.invalid/x",
        fetched_at=NOW - timedelta(days=days_ago),
        parser_version="1",
    )


def _state(log: IngestLog, source: str) -> Freshness:
    return next(h.freshness for h in source_health(log, now=NOW) if h.source_id == source)


class TestItCanActuallyReportIllHealth:
    """The point of the module. Written first because a check that always
    returns 'fine' passes every other test in this file."""

    def test_a_daily_source_silent_for_a_month_is_overdue(self, log: IngestLog) -> None:
        _append(log, "fred", days_ago=30)
        assert _state(log, "fred") is Freshness.OVERDUE

    def test_overdue_names_how_far_past_tolerance_it_is(self, log: IngestLog) -> None:
        """ "Late" and "a month late" prompt different actions, and a state
        alone cannot tell them apart."""
        _append(log, "fred", days_ago=30)
        sick = next(h for h in source_health(log, now=NOW) if h.source_id == "fred")
        # Tolerance for a 1-day cadence is 1*2+1 = 3 days.
        assert sick.overdue_by_days == pytest.approx(27.0)
        assert "27.0d past tolerance" in sick.explain()

    def test_the_overdue_helper_returns_only_the_broken_ones(self, log: IngestLog) -> None:
        _append(log, "fred", days_ago=30)
        _append(log, "gleif-rr", days_ago=0.5)
        stopped = {h.source_id for h in overdue(source_health(log, now=NOW))}
        assert "fred" in stopped
        assert "gleif-rr" not in stopped


class TestTheGraceAbsorbsWeekendsAndNotOutages:
    """A daily source is three days old on a Monday morning and is fine.
    A report that called that broken would be ignored within a week, which
    is the failure mode that matters more than a missed alarm."""

    @pytest.mark.parametrize("days", [0.0, 1.0, 2.5, 3.0])
    def test_a_daily_source_within_the_grace_is_fresh(self, log: IngestLog, days: float) -> None:
        _append(log, "fred", days_ago=days)
        assert _state(log, "fred") is Freshness.FRESH

    def test_just_past_the_grace_is_overdue(self, log: IngestLog) -> None:
        """3.0 days is fresh and 3.1 is not, so the boundary is where it is
        documented to be rather than wherever the arithmetic landed."""
        _append(log, "fred", days_ago=3.1)
        assert _state(log, "fred") is Freshness.OVERDUE

    def test_a_monthly_source_quiet_for_a_fortnight_is_fine(self, log: IngestLog) -> None:
        """The cadence is per-source, so a monthly file must not be judged
        against a daily feed's expectations."""
        _append(log, "ecb-hicp", days_ago=14)
        assert _state(log, "ecb-hicp") is Freshness.FRESH


class TestTheThreeQuietStatesAreDistinguished:
    """All three render as 'no recent data' if collapsed, and each needs a
    different response: wire it up, fix it, or leave it alone."""

    def test_never_fetched_is_not_overdue(self, log: IngestLog) -> None:
        """Nothing is broken — it has not been wired into a run. Reporting
        it as broken sends someone to debug a working endpoint."""
        assert _state(log, "fred") is Freshness.NEVER

    def test_a_source_with_no_declared_cadence_is_not_judged(self, log: IngestLog) -> None:
        """OpenFIGI is a lookup, not a feed. Inventing a cadence for it
        would generate a permanent false alarm, and a report that cries
        wolf gets turned off."""
        _append(log, "openfigi", days_ago=400)
        assert _state(log, "openfigi") is Freshness.IRREGULAR

    def test_an_irregular_source_still_reports_its_volume(self, log: IngestLog) -> None:
        _append(log, "openfigi", days_ago=400)
        entry = next(h for h in source_health(log, now=NOW) if h.source_id == "openfigi")
        assert entry.payloads == 1
        assert "not judged" in entry.explain()


class TestItCoversEverySource:
    def test_a_registered_source_appears_even_with_an_empty_log(self, log: IngestLog) -> None:
        """Reporting only on sources that appear in the log would hide the
        one most likely to have been forgotten."""
        from treble.ingest.registry import all_sources

        reported = {h.source_id for h in source_health(log, now=NOW)}
        assert reported == set(all_sources())

    def test_every_adapter_declares_a_cadence_decision(self) -> None:
        """None is a valid answer and a missing field is not. This is the
        assertion that keeps a new adapter from joining the roster without
        anyone deciding how often it should be heard from."""
        from treble.ingest.base import SourceMeta
        from treble.ingest.registry import all_sources

        for source_id, meta in all_sources().items():
            assert isinstance(meta, SourceMeta), source_id
            assert hasattr(meta, "expected_cadence_days"), source_id

    def test_the_worst_news_is_first(self, log: IngestLog) -> None:
        """A broken source buried alphabetically among twenty healthy ones
        is a report that gets read once."""
        _append(log, "gleif-rr", days_ago=0.5)
        _append(log, "fred", days_ago=30)
        assert source_health(log, now=NOW)[0].source_id == "fred"

    def test_the_latest_fetch_wins_not_the_first(self, log: IngestLog) -> None:
        """A source ingested daily for a year and then stopped has an old
        first entry and a recent last one. Taking the wrong end would call
        a healthy source broken, or — far worse — the reverse."""
        _append(log, "fred", days_ago=300)
        _append(log, "fred", days_ago=0.5)
        assert _state(log, "fred") is Freshness.FRESH
