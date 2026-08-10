"""TVAL snapshot times (spec §15.5).

> "TVAL publishes at multiple globally relevant times — 3pm and 4pm New
> York, 4:15pm London, Tokyo close — because a global fund needs consistent
> valuation timing across its book. Bid, mid, and ask evaluations are
> produced for each."

This was recorded as data-blocked: "needs intraday quote captures no free
source here provides." That conflated two different absences. The *data*
for corporate bonds is indeed missing, and stays missing. The *machinery* —
knowing when 4pm New York is, in UTC, on an arbitrary date — was simply
never built, and nothing about it depends on having quotes. It is the same
mistake as the product catalogue that read "HICP stored" on a store with no
HICP: an absence of data recorded as though it settled a question about
code.

**The times are the hard part, and they are hard for one reason.** "4pm New
York" is 20:00 UTC for part of the year and 21:00 UTC for the rest, and the
changeover dates differ between New York and London — the US springs
forward two weeks before the EU does, so for a fortnight each March the
usual five-hour gap between London and New York is four. A snapshot series
built on fixed UTC offsets is correct for most of the year and silently
an hour wrong the rest, which is the worst available failure: it reconciles
against itself, every day, until someone compares against a counterparty.

So offsets are never written down here. Each time is a zone plus a wall
clock, resolved through the IANA database at the moment it is asked for.

Tokyo is the exception that proves the rule: Japan has observed no daylight
saving since 1951, so its close is a fixed offset — and it is still written
as a zone, because a constant that happens to be true is indistinguishable
from one that is guaranteed, and only one of them survives a rule change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from treble.analytics.registry import model
from treble.plant.quotes import Book

#: The four publication times §15.5 names. Ordered by the UTC instant they
#: fall at on a normal day — Tokyo first — because a series presented out
#: of chronological order invites reading a later mark as an earlier one.
SNAPSHOT_TIMES: tuple[SnapshotTime, ...]


@dataclass(frozen=True)
class SnapshotTime:
    """One globally relevant valuation time.

    A zone and a wall clock, never an offset. See the module docstring: an
    offset is correct until it is not, and the day it stops being correct
    is a day nothing complains.
    """

    name: str
    zone: str
    local: time

    def at(self, day: date) -> datetime:
        """The UTC instant this snapshot falls at on `day`.

        `day` is the local calendar date in this snapshot's own zone, not a
        UTC date. Tokyo's close on 3 March is a moment that falls on 2 March
        in New York, and resolving it against a UTC date would move the
        Tokyo mark a day whenever the two disagree.
        """
        return datetime.combine(day, self.local, tzinfo=ZoneInfo(self.zone)).astimezone(UTC)


SNAPSHOT_TIMES = (
    SnapshotTime("Tokyo close", "Asia/Tokyo", time(15, 0)),
    SnapshotTime("London 16:15", "Europe/London", time(16, 15)),
    SnapshotTime("New York 15:00", "America/New_York", time(15, 0)),
    SnapshotTime("New York 16:00", "America/New_York", time(16, 0)),
)


@dataclass(frozen=True)
class Snapshot:
    """A bid/mid/ask evaluation at one publication time."""

    time_name: str
    at: datetime
    bid: float | None
    ask: float | None
    #: How many quotes were live at that instant. Zero is a real answer and
    #: renders differently from a missing snapshot.
    contributors: int

    @property
    def mid(self) -> float | None:
        """The mid, or None when either side is absent.

        Not a one-sided fallback. A "mid" built from a bid alone is a bid
        wearing a different label, and a fund reconciling against it would
        be comparing its bid to someone else's mid without either side
        knowing.
        """
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2.0

    @property
    def is_empty(self) -> bool:
        return self.contributors == 0


@dataclass(frozen=True)
class SnapshotSeries:
    """One instrument's published valuations across the day."""

    snapshots: tuple[Snapshot, ...]

    @property
    def unchanged(self) -> bool:
        """Whether every populated snapshot carries the same two sides.

        A property of the series rather than a free function, and that is
        not only tidiness: as a module-level callable in `treble.analytics`
        it would have owed an I3 model envelope, which would have stamped a
        yes/no comparison as though a model had made a judgement. It reads
        a result that already carries identity.

        Published rather than left for a reader to infer. On an install
        with no intraday captures, four identical rows are the *correct*
        answer and look exactly like four independent evaluations that
        happened to agree — which would be a remarkable coincidence, and is
        in fact a statement that nothing moved between them.
        """
        marks = {(s.bid, s.ask) for s in self.snapshots if not s.is_empty}
        return len(marks) <= 1

    @property
    def all_empty(self) -> bool:
        return all(s.is_empty for s in self.snapshots)


@model(
    model_id="tval.snapshot_series",
    version="1.0",
    spec_section="§15.5",
    summary="Bid/mid/ask at each globally relevant publication time, zone-resolved.",
)
def snapshot_series(
    books: dict[str, Book],
    *,
    day: date,
    times: tuple[SnapshotTime, ...] = SNAPSHOT_TIMES,
) -> SnapshotSeries:
    """Evaluate one instrument at each publication time.

    `books` maps the resolved UTC instant's ISO string to the book as it
    stood then — the caller reads the book once per instant, because only
    the caller knows where books come from (a live service here, a stored
    capture later, a vendor file in Phase 3).
    """
    out: list[Snapshot] = []
    for snapshot_time in times:
        at = snapshot_time.at(day)
        book = books.get(at.isoformat())
        if book is None:
            out.append(
                Snapshot(time_name=snapshot_time.name, at=at, bid=None, ask=None, contributors=0)
            )
            continue
        bid, ask = book.tcmp
        if bid is None and ask is None:
            # Fall back to indicative before giving up: an indicative mark
            # is weaker than an executable one and is still a mark, and
            # §15.5 asks for a published level rather than a tradable one.
            bid, ask = book.tgn
        out.append(
            Snapshot(
                time_name=snapshot_time.name,
                at=at,
                bid=bid,
                ask=ask,
                contributors=len(book.quotes),
            )
        )
    return SnapshotSeries(snapshots=tuple(out))


__all__ = [
    "SNAPSHOT_TIMES",
    "Snapshot",
    "SnapshotSeries",
    "SnapshotTime",
    "snapshot_series",
]
