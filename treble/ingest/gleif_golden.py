"""GLEIF Golden Copy publishes, and choosing the smallest file that is enough.

The RR adapter downloaded the **full** concatenated relationship file every
run: 31.45 MB, 486,115 records, every one of them re-asserting a
relationship that had not changed. Measured on this install it was
37.27 MB per fetch against a declared one-day cadence — **13.60 GB a year**,
the single largest line in the disk projection, on a laptop with 9 GB free.

GLEIF publishes deltas beside every golden copy. Measured 2026-09-01:

    rr full file       31.45 MB    486,115 records
    rr LastMonth        3.30 MB     53,369
    rr LastWeek       597.68 KB      9,419
    rr LastDay         90.55 KB      1,536   <- 347x smaller than full

So the daily cost falls from 37 MB to about 90 KB, and the yearly figure
from 13.60 GB to roughly 33 MB.

## Why this needs no parser change

A delta is the same RR-CDF document with fewer records — same namespace,
same root, same `RelationshipRecord` elements. Verified by running the
unmodified parser over a live LastDay file: 1,536 facts, matching the record
count the API declared for it exactly. `parser_version` therefore does not
move, and every payload already stored replays exactly as before (I5).

## Why a delta is safe under I2

The store is append-only and bitemporal. A full file re-asserts every
relationship; a delta asserts only the ones that changed. Facts from the
last full file stay visible until something supersedes them, so
`base + deltas` and `full file` resolve to the same answer for every
`as_of` — the delta simply stops writing down the 484,579 records that had
nothing to say. Which is the same argument `store/coalesce.py` makes, one
layer earlier: coalescing stopped *storing* the unchanged rows, and this
stops *downloading* them.

## The part that has to be right

A delta only covers a window. If more time has passed since the last fetch
than the delta reaches back, the records in between are missed — silently,
because a short file and an uneventful day look identical.

So the window is chosen from the gap **and then verified against the file's
own `DeltaStart` header**, which states the instant it actually covers. A
file that does not reach back to what the store already knows is refused and
the full copy taken instead. The check is on the downloaded file rather than
on arithmetic about publication schedules, because the schedule is GLEIF's
to change and the header is a fact.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

#: The publishes index. Page size 1 — only the newest publish is ever
#: wanted, and the default page returns fifteen of them with every delta
#: URL for each, which is a megabyte of JSON to read four fields from.
PUBLISHES_URL = "https://goldencopy.gleif.org/api/v2/golden-copies/publishes?per_page=1"


class Window(enum.Enum):
    """How far back a published file reaches.

    `IntraDay` is deliberately absent. It covers only the hours since the
    previous publish of the same day, so choosing it correctly depends on
    knowing GLEIF's intra-day schedule — and a source that must know a
    vendor's publication times to stay correct breaks quietly when the
    vendor changes them. `LastDay` is 90 KB; the saving is not worth it.
    """

    LAST_DAY = "LastDay"
    LAST_WEEK = "LastWeek"
    LAST_MONTH = "LastMonth"
    FULL = "full"


#: What each window reaches back, by GLEIF's own naming. `FULL` is absent
#: rather than given an enormous value: it is the fallback, not a window,
#: and giving it a number would let a gap calculation "choose" it by
#: comparison and hide the fact that nothing else was enough.
COVERAGE: dict[Window, timedelta] = {
    Window.LAST_DAY: timedelta(days=1),
    Window.LAST_WEEK: timedelta(days=7),
    Window.LAST_MONTH: timedelta(days=30),
}

#: Escalating order, smallest first.
LADDER: tuple[Window, ...] = (Window.LAST_DAY, Window.LAST_WEEK, Window.LAST_MONTH, Window.FULL)


def choose_window(gap: timedelta | None) -> Window:
    """The smallest window covering ``gap``. Pure.

    ``gap`` is measured from what the store already knows to **what the new
    file describes** — its publish date — not to "now". Those differ by the
    hours between GLEIF publishing and this machine getting round to
    fetching, and measuring to `now` charges the delta for time no file has
    to account for. On a daily schedule it is the difference between a
    24-hour gap (`LastDay`, 90 KB) and a 32-hour one (`LastWeek`, 598 KB) —
    a 6.6x cost for an interval that does not exist.

    Optimistic on purpose, and safe because it is not the last word:
    :func:`covers` checks the downloaded file's own `DeltaStart` and the
    caller escalates if it falls short. A margin here would be a guess
    about GLEIF's publication schedule standing in for a fact the file
    states outright. The observed LastDay file reaches back 32 hours, not
    24 — which is exactly the kind of thing that is true today, undocumented,
    and not worth depending on in either direction.

    ``None`` — nothing fetched yet — is the full copy, and must be: a delta
    with no base underneath it is a handful of changes presented as the
    whole relationship graph.
    """
    if gap is None:
        return Window.FULL
    for window in LADDER:
        if window is Window.FULL or COVERAGE[window] >= gap:
            return window
    return Window.FULL  # pragma: no cover - LADDER ends in FULL


@dataclass(frozen=True)
class PublishedFile:
    """One downloadable file from a publish."""

    url: str
    window: Window
    size: int
    record_count: int


def select_file(publishes: object, kind: str, window: Window) -> PublishedFile:
    """Pick ``kind``'s file for ``window`` out of the publishes response.

    Raises rather than falling back to another window: a fetch that quietly
    substituted a different file would log a payload whose coverage nobody
    checked, which is the failure this module exists to prevent.
    """
    if not isinstance(publishes, dict):
        raise ValueError("GLEIF publishes response was not an object")
    data = publishes.get("data")
    if not isinstance(data, list) or not data:
        raise ValueError("GLEIF publishes response carried no publishes")
    section = data[0].get(kind) if isinstance(data[0], dict) else None
    if not isinstance(section, dict):
        raise ValueError(f"newest GLEIF publish has no {kind!r} section")

    if window is Window.FULL:
        entry = (section.get("full_file") or {}).get("xml")
    else:
        entry = ((section.get("delta_files") or {}).get(window.value) or {}).get("xml")
    if not isinstance(entry, dict) or not entry.get("url"):
        raise ValueError(f"GLEIF publish has no {kind} {window.value} XML file")

    return PublishedFile(
        url=str(entry["url"]),
        window=window,
        size=int(entry.get("size") or 0),
        record_count=int(entry.get("record_count") or 0),
    )


#: `DeltaStart` sits in the header at the top of the document. Read with a
#: regex over the first few kilobytes rather than by parsing: the full copy
#: is 31 MB of XML and this is one field, needed before the decision to
#: keep the file at all.
_DELTA_START = re.compile(rb"<[a-zA-Z0-9]*:?DeltaStart>([^<]+)<")
_HEADER_BYTES = 4096


def delta_start(data: bytes) -> datetime | None:
    """When the delta's window opens, or ``None`` for a full copy.

    A full copy has no `DeltaStart`, and that absence is meaningful — it
    covers everything — so it is returned as ``None`` rather than as a very
    old date that a comparison would treat as "covers the gap" by accident.
    """
    match = _DELTA_START.search(data[:_HEADER_BYTES])
    if match is None:
        return None
    return _parse_instant(match.group(1).decode("ascii", "replace"))


_CONTENT_DATE = re.compile(rb"<[a-zA-Z0-9]*:?ContentDate>([^<]+)<")


def content_date(data: bytes) -> datetime | None:
    """The instant the file's contents describe.

    This, not the time it was fetched, is how current the store is. A file
    published at 08:00 and fetched at 23:00 leaves the store fifteen hours
    behind what the fetch timestamp claims, and a delta chosen against the
    fetch time would skip exactly that interval.
    """
    match = _CONTENT_DATE.search(data[:_HEADER_BYTES])
    if match is None:
        return None
    return _parse_instant(match.group(1).decode("ascii", "replace"))


def _parse_instant(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def covers(data: bytes, *, known_through: datetime | None) -> bool:
    """Whether ``data`` continues the store without a hole.

    True for a full copy always. For a delta, true only when its window
    opens at or before what the store already knows — so applying it leaves
    no interval unaccounted for.
    """
    start = delta_start(data)
    if start is None:
        return True
    if known_through is None:
        return False
    return start <= known_through


__all__ = [
    "COVERAGE",
    "LADDER",
    "PUBLISHES_URL",
    "PublishedFile",
    "Window",
    "choose_window",
    "content_date",
    "covers",
    "delta_start",
    "select_file",
]
