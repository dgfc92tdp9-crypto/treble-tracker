"""Choosing the smallest GLEIF file that is enough, and refusing one that isn't.

The RR adapter downloaded the full 31 MB concatenated file every run —
13.60 GB a year at its declared daily cadence, the largest single line in
the disk projection. GLEIF publishes deltas beside every golden copy, and
the LastDay file is 90 KB.

The saving is only safe if a delta that does not reach back far enough is
caught, because a short delta and an uneventful day produce the same thing:
a small file with few records. So the tests that matter here are the ones
about *coverage*, not the ones about size.

`golden_publishes.json` is the live publishes index of 2026-09-01, trimmed
to the RR section and its XML files. The sizes and record counts in it are
real.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from treble.ingest.gleif_golden import (
    COVERAGE,
    LADDER,
    Window,
    choose_window,
    content_date,
    covers,
    delta_start,
    select_file,
)

PUBLISHES = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "gleif" / "golden_publishes.json").read_text()
)

#: The header of the live LastDay file, downloaded 2026-09-01. Note that it
#: reaches back 32 hours from an 08:00 publish, not 24 — undocumented, true
#: on the day, and deliberately not depended on in either direction.
DELTA_HEADER = (
    b'<?xml version="1.0" encoding="UTF-8"?><rr:RelationshipData '
    b'xmlns:rr="http://www.gleif.org/data/schema/rr/2016">\n'
    b"  <rr:Header>\n"
    b"    <rr:ContentDate>2026-09-01T08:00:00Z</rr:ContentDate>\n"
    b"    <rr:FileContent>GLEIF_DELTA_PUBLISHED</rr:FileContent>\n"
    b"    <rr:DeltaStart>2026-08-31T00:00:00Z</rr:DeltaStart>\n"
    b"  </rr:Header>\n"
)

FULL_HEADER = (
    b'<?xml version="1.0" encoding="UTF-8"?><rr:RelationshipData '
    b'xmlns:rr="http://www.gleif.org/data/schema/rr/2016">\n'
    b"  <rr:Header>\n"
    b"    <rr:ContentDate>2026-09-01T08:00:00Z</rr:ContentDate>\n"
    b"    <rr:FileContent>GLEIF_FULL_PUBLISHED</rr:FileContent>\n"
    b"  </rr:Header>\n"
)


class TestChoosingAWindow:
    def test_nothing_stored_yet_takes_the_full_copy(self) -> None:
        """A delta with no base underneath it is a handful of changes
        presented as the whole relationship graph."""
        assert choose_window(None) is Window.FULL

    @pytest.mark.parametrize(
        ("gap", "expected"),
        [
            (timedelta(hours=8), Window.LAST_DAY),
            (timedelta(days=1), Window.LAST_DAY),
            (timedelta(days=2), Window.LAST_WEEK),
            (timedelta(days=7), Window.LAST_WEEK),
            (timedelta(days=8), Window.LAST_MONTH),
            (timedelta(days=30), Window.LAST_MONTH),
            (timedelta(days=31), Window.FULL),
        ],
    )
    def test_the_smallest_window_that_covers_the_gap(
        self, gap: timedelta, expected: Window
    ) -> None:
        assert choose_window(gap) is expected

    def test_a_daily_schedule_lands_on_the_cheapest_file(self) -> None:
        """The case the whole change is for. A gap measured to *now* rather
        than to the publish date would be ~32 hours on a daily schedule and
        would select LastWeek — 598 KB instead of 90 KB, a 6.6x cost for an
        interval no file has to account for."""
        assert choose_window(timedelta(days=1)) is Window.LAST_DAY

    def test_the_ladder_ends_in_the_full_copy(self) -> None:
        """So no gap, however large, falls off the end without an answer."""
        assert LADDER[-1] is Window.FULL
        assert choose_window(timedelta(days=3650)) is Window.FULL

    def test_every_window_but_the_full_copy_declares_its_coverage(self) -> None:
        assert set(COVERAGE) == set(LADDER) - {Window.FULL}


class TestSelectingTheFile:
    def test_the_full_copy(self) -> None:
        chosen = select_file(PUBLISHES, "rr", Window.FULL)
        assert chosen.url.endswith("rr-golden-copy.xml.zip")
        assert chosen.record_count == 486_115

    def test_a_delta(self) -> None:
        chosen = select_file(PUBLISHES, "rr", Window.LAST_DAY)
        assert chosen.url.endswith("rr-last-day.xml.zip")
        assert chosen.record_count == 1_536

    def test_the_delta_really_is_the_smaller_file(self) -> None:
        """Guards the reason for the change against the fixture drifting
        into something that would make it pointless."""
        full = select_file(PUBLISHES, "rr", Window.FULL)
        delta = select_file(PUBLISHES, "rr", Window.LAST_DAY)
        assert delta.size * 100 < full.size

    @pytest.mark.parametrize(
        "payload",
        [
            "not an object",
            {},
            {"data": []},
            {"data": [{}]},
            {"data": [{"rr": {}}]},
            {"data": [{"rr": {"delta_files": {}}}]},
        ],
    )
    def test_a_response_it_cannot_read_raises(self, payload: object) -> None:
        """Rather than falling back to another window. A fetch that quietly
        substituted a different file would log a payload whose coverage
        nobody checked."""
        with pytest.raises(ValueError):
            select_file(payload, "rr", Window.LAST_DAY)


class TestReadingTheHeader:
    def test_a_delta_states_when_its_window_opens(self) -> None:
        assert delta_start(DELTA_HEADER) == datetime(2026, 8, 31, tzinfo=UTC)

    def test_a_full_copy_has_no_delta_start(self) -> None:
        assert delta_start(FULL_HEADER) is None

    def test_the_content_date_is_read(self) -> None:
        assert content_date(DELTA_HEADER) == datetime(2026, 9, 1, 8, 0, tzinfo=UTC)

    def test_an_unreadable_header_is_none_rather_than_a_guess(self) -> None:
        assert content_date(b"<html>not xml at all</html>") is None
        assert delta_start(b"") is None

    def test_a_malformed_instant_is_none(self) -> None:
        assert content_date(b"<rr:ContentDate>the day before yesterday</rr:ContentDate>") is None

    def test_the_header_is_read_without_scanning_the_whole_file(self) -> None:
        """The full copy is 958 MB of XML and this is one field, needed
        before the decision to keep the file at all. A `DeltaStart` far
        past the header is not a header and must not be read as one."""
        buried = b"<x>" + b"." * 100_000 + b"<rr:DeltaStart>2026-08-31T00:00:00Z</rr:DeltaStart>"
        assert delta_start(buried) is None


class TestCoverage:
    """The check the saving rests on."""

    KNOWN = datetime(2026, 9, 1, tzinfo=UTC)

    def test_a_delta_reaching_back_far_enough_is_accepted(self) -> None:
        assert covers(DELTA_HEADER, known_through=self.KNOWN)

    def test_a_delta_that_starts_after_what_is_known_is_refused(self) -> None:
        """The failure this exists to catch: the interval between what the
        store knows and where the delta begins would be lost, silently,
        because a short file and a quiet day look identical."""
        stale = DELTA_HEADER.replace(b"2026-08-31T00:00:00Z", b"2026-09-05T00:00:00Z")
        assert not covers(stale, known_through=self.KNOWN)

    def test_a_full_copy_always_covers(self) -> None:
        assert covers(FULL_HEADER, known_through=None)
        assert covers(FULL_HEADER, known_through=self.KNOWN)

    def test_a_delta_onto_an_empty_store_is_refused(self) -> None:
        """Nothing is known, so nothing can be continued."""
        assert not covers(DELTA_HEADER, known_through=None)

    def test_the_boundary_is_inclusive(self) -> None:
        """A window opening exactly where knowledge ends leaves no hole."""
        assert covers(DELTA_HEADER, known_through=datetime(2026, 8, 31, tzinfo=UTC))
        assert not covers(DELTA_HEADER, known_through=datetime(2026, 8, 30, 23, 59, 59, tzinfo=UTC))
