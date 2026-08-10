"""TVAL snapshot times (spec §15.5).

Almost every test here is about daylight saving, because that is the only
thing in this module that can be wrong in a way nothing notices. A snapshot
series built on fixed UTC offsets reconciles against itself perfectly, every
day, and is an hour out for several months of the year.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from treble.analytics.tval.snapshots import (
    SNAPSHOT_TIMES,
    Snapshot,
    SnapshotSeries,
    SnapshotTime,
    snapshot_series,
)
from treble.core.identifiers import TUID
from treble.plant.quotes import Book, Firmness, Quote

#: The undecorated function. The suite asserts on the series itself; the I3
#: envelope around it is `test_i3_registry`'s business.
_series = snapshot_series.__wrapped__  # type: ignore[attr-defined]

DAY = date(2026, 3, 10)


def _when(name: str, day: date = DAY) -> datetime:
    """The resolved UTC instant for a named snapshot."""
    return next(t for t in SNAPSHOT_TIMES if t.name == name).at(day)


def _at(name: str, day: date = DAY) -> str:
    return _when(name, day).strftime("%H:%M")


class TestTheTimesTrackDaylightSaving:
    """The reason the module exists."""

    def test_new_york_moves_with_us_daylight_saving(self) -> None:
        """4pm New York is 21:00 UTC in winter and 20:00 in summer. A fixed
        offset is right for one of those and wrong for the other."""
        assert _at("New York 16:00", date(2026, 1, 15)) == "21:00"
        assert _at("New York 16:00", date(2026, 7, 15)) == "20:00"

    def test_london_moves_on_a_different_date_from_new_york(self) -> None:
        """The case that makes fixed offsets indefensible. The US springs
        forward two weeks before the EU, so for a fortnight each March the
        London-to-New-York gap is four hours rather than five. Anything
        that hard-codes five is wrong here and nowhere else — which is
        precisely why it would survive review."""
        march = _when("New York 15:00") - _when("London 16:15")
        july = _when("New York 15:00", date(2026, 7, 15)) - _when("London 16:15", date(2026, 7, 15))
        assert march == timedelta(hours=2, minutes=45)
        # The same pair is 45 minutes further apart once London catches up,
        # which is the whole point: the gap is not a constant.
        assert july == timedelta(hours=3, minutes=45)

    def test_tokyo_never_moves(self) -> None:
        """Japan has observed no daylight saving since 1951. Written as a
        zone anyway: a constant that happens to hold is indistinguishable
        from one that is guaranteed, and only one survives a rule change."""
        assert {_at("Tokyo close", d) for d in (date(2026, 1, 15), date(2026, 7, 15))} == {"06:00"}

    def test_every_snapshot_is_timezone_aware_utc(self) -> None:
        """A naive instant would make the cut depend on the machine that
        computed it, which is the same class of defect as a naive as_of."""
        for snapshot_time in SNAPSHOT_TIMES:
            assert snapshot_time.at(DAY).tzinfo is UTC

    def test_the_day_is_local_not_utc(self) -> None:
        """Tokyo's close on 3 March falls on 2 March in New York. Resolving
        against a UTC date would move the Tokyo mark a day whenever the two
        disagree."""
        tokyo = next(t for t in SNAPSHOT_TIMES if t.name == "Tokyo close")
        assert tokyo.at(date(2026, 3, 3)).date() == date(2026, 3, 3)
        # 06:00Z — same UTC day here, but the local date is what was asked
        # for, and the assertion above is what pins that.
        assert tokyo.at(date(2026, 3, 3)).hour == 6


class TestTheSeriesIsOrderedAndComplete:
    def test_all_four_spec_times_are_present(self) -> None:
        assert len(SNAPSHOT_TIMES) == 4
        assert {t.name for t in SNAPSHOT_TIMES} == {
            "Tokyo close",
            "London 16:15",
            "New York 15:00",
            "New York 16:00",
        }

    def test_they_are_in_chronological_order(self) -> None:
        """A series out of order invites reading a later mark as an earlier
        one, which is exactly the confusion multiple publication times
        exist to remove."""
        instants = [t.at(DAY) for t in SNAPSHOT_TIMES]
        assert instants == sorted(instants)


SUBJECT = TUID("isin:US0000000001")


def _book(bid: float | None, ask: float | None) -> Book:
    when = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    return Book(
        subject=SUBJECT,
        quotes=(
            Quote(
                subject=SUBJECT,
                contributor="BANK-A",
                firmness=Firmness.EXECUTABLE,
                bid=bid,
                ask=ask,
                bid_size=1e6,
                ask_size=1e6,
                quoted_at=when,
            ),
        ),
    )


class TestTheEvaluations:
    @staticmethod
    def _all_times(book: Book) -> dict[str, Book]:
        return {t.at(DAY).isoformat(): book for t in SNAPSHOT_TIMES}

    def test_each_time_gets_a_bid_and_an_ask(self) -> None:
        series = _series(self._all_times(_book(99.0, 99.5)), day=DAY)
        assert len(series.snapshots) == 4
        assert all(s.bid == 99.0 and s.ask == 99.5 for s in series.snapshots)

    def test_the_mid_is_the_midpoint(self) -> None:
        series = _series(self._all_times(_book(99.0, 99.5)), day=DAY)
        assert series.snapshots[0].mid == pytest.approx(99.25)

    def test_a_one_sided_book_has_no_mid(self) -> None:
        """A "mid" built from a bid alone is a bid wearing a different
        label, and a fund reconciling against it compares its bid to
        someone else's mid without either side knowing."""
        series = _series(self._all_times(_book(99.0, None)), day=DAY)
        assert series.snapshots[0].bid == 99.0
        assert series.snapshots[0].mid is None

    def test_a_time_with_no_book_is_empty_not_absent(self) -> None:
        """A missing snapshot and a snapshot of an empty book are different
        facts, and a series that dropped the row would renumber the rest."""
        series = _series({}, day=DAY)
        assert len(series.snapshots) == 4
        assert series.all_empty
        assert all(s.bid is None for s in series.snapshots)


class TestItSaysWhenNothingMoved:
    """On an install with no intraday captures, four identical rows are the
    correct answer and look exactly like four independent evaluations that
    agreed. Those are very different claims."""

    def test_identical_marks_are_reported_as_unchanged(self) -> None:
        assert _series(TestTheEvaluations._all_times(_book(99.0, 99.5)), day=DAY).unchanged

    def test_a_moving_market_is_not_reported_as_unchanged(self) -> None:
        books = {
            t.at(DAY).isoformat(): _book(99.0 + i * 0.25, 99.5 + i * 0.25)
            for i, t in enumerate(SNAPSHOT_TIMES)
        }
        assert _series(books, day=DAY).unchanged is False

    def test_empty_snapshots_do_not_count_as_agreement(self) -> None:
        """Two empty rows and one populated one is not three marks that
        agree; it is one mark and two absences."""
        at = datetime(2026, 3, 10, tzinfo=UTC)
        series = SnapshotSeries(
            snapshots=(
                Snapshot(time_name="a", at=at, bid=None, ask=None, contributors=0),
                Snapshot(time_name="b", at=at, bid=99.0, ask=99.5, contributors=1),
            )
        )
        assert series.unchanged is True
        assert series.snapshots[0].is_empty and not series.snapshots[1].is_empty


class TestACustomScheduleIsSupported:
    def test_a_caller_can_supply_its_own_times(self) -> None:
        """A fund with a different book publishes at different times, and
        §15.5's four are a default rather than a limit."""
        sydney = SnapshotTime(
            "Sydney close", "Australia/Sydney", __import__("datetime").time(16, 0)
        )
        series = _series({}, day=DAY, times=(sydney,))
        assert len(series.snapshots) == 1
        assert series.snapshots[0].time_name == "Sydney close"
        # Sydney is on daylight saving in March: 16:00 AEDT is 05:00 UTC.
        assert series.snapshots[0].at.strftime("%H:%M") == "05:00"
