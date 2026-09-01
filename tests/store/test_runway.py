"""Headroom: what the declared cadences will cost, before they cost it.

`test_storage.py` covers waste already on the disk. This covers the other
failure, which looks nothing like it: every byte legitimate, nothing
reclaimable, and the disk full anyway because the cadences add up.

The numbers in the fixtures are the ones measured on the live install on
2026-09-01 — two GLEIF bulk files, 37 MB and 27 MB, both declared daily,
between them 23.3 GB a year against 8.1 GB free.
"""

from __future__ import annotations

from pathlib import Path

from treble.store.storage import (
    RUNWAY_FLOOR_DAYS,
    Growth,
    free_bytes,
    runway_days,
    runway_verdict,
)

GB = 1_000_000_000
MB = 1_000_000

#: gleif-rr and gleif-isin as measured: 37.27 MB and 26.64 MB, daily.
LIVE = Growth(per_day=66 * MB, contributors=(("gleif-rr", 37 * MB), ("gleif-isin", 27 * MB)))


class TestRunway:
    def test_headroom_is_free_space_over_the_daily_rate(self) -> None:
        assert runway_days(100 * MB, Growth(per_day=10 * MB)) == 10.0

    def test_nothing_growing_is_not_infinite_headroom(self) -> None:
        """`None`, not a very large number. "No source declares a cadence"
        and "centuries of headroom" want different responses, and rendering
        both as 99999 days would hide the first behind the second."""
        assert runway_days(100 * MB, Growth(per_day=0)) is None

    def test_the_live_measurement_is_inside_a_year(self) -> None:
        """The finding, pinned: 8.1 GB free against the declared cadences
        is about four months, not the "plenty of room" a 2.7 GB project on
        a 228 GB disk suggests."""
        days = runway_days(8_100 * MB, LIVE)
        assert days is not None
        assert 100 < days < 130


class TestTheVerdict:
    def test_ample_headroom_passes(self) -> None:
        assert runway_verdict(500 * GB, LIVE).ok

    def test_the_live_position_does_not(self) -> None:
        verdict = runway_verdict(8_100 * MB, LIVE)
        assert not verdict.ok

    def test_it_names_the_sources_rather_than_only_the_total(self) -> None:
        """ "24 GB a year" is not actionable. "gleif-rr, 37 MB every day" is
        — it points at a cadence to argue with or a bulk file to replace
        with deltas."""
        reasons = " ".join(runway_verdict(8_100 * MB, LIVE).reasons)
        assert "gleif-rr" in reasons and "gleif-isin" in reasons

    def test_nothing_growing_passes(self) -> None:
        assert runway_verdict(1, Growth(per_day=0)).ok

    def test_the_floor_is_the_thing_being_tested(self) -> None:
        """Proves the verdict turns on the floor rather than on the numbers
        happening to be large: the same position passes and fails as the
        floor moves across it."""
        position = (8_100 * MB, LIVE)
        days = runway_days(*position)
        assert days is not None
        assert runway_verdict(*position, floor_days=days - 1).ok
        assert not runway_verdict(*position, floor_days=days + 1).ok

    def test_the_default_floor_is_six_months(self) -> None:
        assert RUNWAY_FLOOR_DAYS == 180.0


class TestFreeBytes:
    def test_it_reports_the_filesystem_not_the_directory(self, tmp_path: Path) -> None:
        assert free_bytes(tmp_path) > 0

    def test_a_missing_directory_falls_back_to_its_parent(self, tmp_path: Path) -> None:
        """`treble storage` runs before `treble init` has made the data
        directory, and a crash there would be the first thing a new user
        saw."""
        assert free_bytes(tmp_path / "not-created-yet") > 0


class TestTheVerdictReadsCorrectly:
    """`RunwayVerdict` is its own type because reusing the byte-budget one
    printed "0.0 MB reclaimable, within the 0.0 MB budget" beside a warning
    that the disk had four months left."""

    def test_the_summary_is_in_days_not_megabytes(self) -> None:
        summary = runway_verdict(8_100 * MB, LIVE).summary
        assert "days" in summary and "MB" not in summary

    def test_nothing_growing_says_so_rather_than_reporting_a_number(self) -> None:
        verdict = runway_verdict(8_100 * MB, Growth(per_day=0))
        assert verdict.days is None
        assert "declared cadence" in verdict.summary

    def test_it_carries_the_floor_it_was_judged_against(self) -> None:
        """A reader comparing two runs needs to know whether the number or
        the threshold moved."""
        assert runway_verdict(8_100 * MB, LIVE, floor_days=90).floor_days == 90
