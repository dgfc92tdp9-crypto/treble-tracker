"""`mktbar` and `mktvwap` — aggregation over the tape (spec §8.3).

§8.3 names `mktdata`, `mktbar` and `mktvwap` as TAPI services. They were
recorded as blocked on the ticker plant's venue adapters; those now exist
(`treble/plant/venues.py`), and this is what they enable: bars and a
volume-weighted price built from real prints rather than from a daily close.

**A bar is closed by the clock, not by the last tick seen.** A bar whose end
is "whenever the data stopped" silently reports a partial interval as a whole
one, and the most recent bar is always the one a reader trusts most. Bars
here are cut on fixed interval boundaries derived from the epoch, so the same
ticks produce the same bars regardless of when the aggregation ran.

**VWAP refuses rather than substituting.** A volume-weighted average price
over ticks with no size is a simple average wearing a different name, and the
difference is invisible on screen — it is exactly the number a large print
would move and a simple average would not. A bar whose ticks carry no size
reports `vwap=None` and says why.

**The last bar is marked partial.** The interval containing the newest tick
has not finished, so its close is not a close and its volume is not the
interval's volume. Presenting it beside completed bars without a flag is how
a chart shows a spurious drop on every refresh.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from treble.core.identifiers import TUID
from treble.plant.conflation import Tick

#: The epoch every bar boundary is measured from. Fixed so two runs over the
#: same ticks cut the same bars: boundaries derived from the first tick seen
#: would shift the whole grid when an earlier tick arrived.
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Bar:
    """One interval of trading for one instrument."""

    subject: TUID
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    trades: int
    #: Total traded quantity, or None when no tick in the bar carried a size.
    volume: float | None
    #: Volume-weighted average price, or None when volume is unknown. Never
    #: silently replaced by the simple average — see the module docstring.
    vwap: float | None
    #: Whether this interval has finished. The bar containing the newest tick
    #: has not, and its close is not a close.
    complete: bool


class NoTicksError(ValueError):
    """No ticks to aggregate.

    Raised rather than returning an empty list: "no trades in this window"
    and "you asked about an instrument nothing has ever printed for" are
    different answers, and a chart drawing neither cannot tell them apart.
    """


def _bucket(moment: datetime, interval: timedelta) -> datetime:
    """The start of the interval containing `moment`."""
    elapsed = (moment - EPOCH) // interval
    return EPOCH + elapsed * interval


def bars_from_ticks(
    ticks: Iterable[Tick], *, interval: timedelta, now: datetime | None = None
) -> list[Bar]:
    """Aggregate ticks into fixed-interval bars, one series per subject.

    `now` decides which bars are complete: an interval that has not ended by
    `now` is marked partial. Passing it explicitly rather than reading the
    clock keeps this a pure function, which is what lets the same ticks be
    replayed into the same bars (I5).

    Ticks are grouped by subject before ordering, so two instruments'
    interleaved prints cannot produce a bar whose open came from one and
    whose close came from the other.
    """
    if interval <= timedelta(0):
        raise ValueError("a bar interval must be positive; zero or negative closes no interval")

    grouped: dict[tuple[TUID, datetime], list[Tick]] = {}
    for tick in ticks:
        grouped.setdefault((tick.subject, _bucket(tick.exchange_time, interval)), []).append(tick)
    if not grouped:
        raise NoTicksError(
            "no ticks to aggregate; an empty bar series and an instrument that has never "
            "printed are different answers and this refuses rather than conflating them"
        )

    cutoff = (
        now
        if now is not None
        else max(t.exchange_time for group in grouped.values() for t in group)
    )

    out: list[Bar] = []
    for (subject, start), group in sorted(
        grouped.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])
    ):
        # Sequence, not timestamp: two prints can share a venue timestamp,
        # and the sequence is what the venue says came first.
        ordered = sorted(group, key=lambda t: t.sequence)
        prices = [t.value for t in ordered]
        sizes = [t.size for t in ordered]

        # All-or-nothing on size. A bar mixing sized and unsized ticks would
        # produce a VWAP weighted over part of its own volume, which is a
        # number with no meaning and no way to notice.
        volume: float | None = None
        vwap: float | None = None
        if all(size is not None for size in sizes):
            total = sum(size for size in sizes if size is not None)
            volume = total
            if total > 0:
                vwap = (
                    sum(
                        price * size
                        for price, size in zip(prices, sizes, strict=True)
                        if size is not None
                    )
                    / total
                )

        end = start + interval
        out.append(
            Bar(
                subject=subject,
                start=start,
                end=end,
                open=prices[0],
                high=max(prices),
                low=min(prices),
                close=prices[-1],
                trades=len(ordered),
                volume=volume,
                vwap=vwap,
                complete=end <= cutoff,
            )
        )
    return out


@dataclass(frozen=True)
class Vwap:
    """A volume-weighted average price over a window, and what it covered."""

    subject: TUID
    first: datetime
    last: datetime
    trades: int
    volume: float
    price: float


def vwap_over(ticks: Sequence[Tick]) -> Vwap:
    """VWAP across every tick given, for one subject.

    Refuses a mixed-subject sequence rather than averaging across
    instruments: the result would be a number with units nobody could name.
    Refuses unsized ticks for the reason in the module docstring.
    """
    if not ticks:
        raise NoTicksError("no ticks to average; a VWAP over nothing is not zero")
    subjects = {tick.subject for tick in ticks}
    if len(subjects) > 1:
        raise ValueError(
            f"a VWAP across {len(subjects)} instruments has no meaning; "
            f"got {sorted(str(s) for s in subjects)}"
        )
    if any(tick.size is None for tick in ticks):
        raise ValueError(
            "some ticks carry no size, so this cannot be volume-weighted. Weighting them "
            "equally would return a simple average under a volume-weighted name — the "
            "number a large print moves and a simple average does not"
        )
    volume = sum(tick.size or 0.0 for tick in ticks)
    if volume <= 0:
        raise ValueError("total traded volume is zero; there is nothing to weight by")
    price = sum(tick.value * (tick.size or 0.0) for tick in ticks) / volume
    return Vwap(
        subject=next(iter(subjects)),
        first=min(tick.exchange_time for tick in ticks),
        last=max(tick.exchange_time for tick in ticks),
        trades=len(ticks),
        volume=volume,
        price=price,
    )


class TickHistory:
    """Retained ticks per instrument, for aggregation.

    Needed because `TickerPlant.tpipe()` *drains*: it is the machine path and
    has one consumer. Aggregating bars off it would consume the stream out
    from under whatever else was reading it, and the second caller would get
    an empty tape rather than the same one.

    So history is recorded alongside rather than taken from the pipe.
    Bounded per instrument, and the bound is enforced by dropping the oldest
    — which is honest for a rolling window and is why `bars_from_ticks`
    reports the interval each bar covers rather than assuming completeness.
    """

    def __init__(self, *, per_subject: int = 100_000) -> None:
        if per_subject <= 0:
            raise ValueError("a history retaining nothing cannot aggregate anything")
        self._per_subject = per_subject
        self._ticks: dict[TUID, deque[Tick]] = {}

    def record(self, tick: Tick) -> None:
        bucket = self._ticks.setdefault(tick.subject, deque(maxlen=self._per_subject))
        bucket.append(tick)

    def ticks(self, subject: TUID) -> tuple[Tick, ...]:
        return tuple(self._ticks.get(subject, ()))

    def subjects(self) -> tuple[TUID, ...]:
        return tuple(sorted(self._ticks))

    def __len__(self) -> int:
        return sum(len(bucket) for bucket in self._ticks.values())


__all__ = [
    "EPOCH",
    "Bar",
    "NoTicksError",
    "TickHistory",
    "Vwap",
    "bars_from_ticks",
    "vwap_over",
]
