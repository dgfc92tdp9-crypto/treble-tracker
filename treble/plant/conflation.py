"""The ticker plant's core: the conflated/unconflated split (spec §6.2, §8.2).

    Display feeds are conflated.   TPIPE delivers unconflated full-tick.

That sentence is the whole design. A human watching a screen cannot consume
500 updates a second, so the display path shows the latest value and drops
what it superseded. A machine reading TPIPE needs every print, because a
dropped tick is a trade that never happened as far as its consumer knows.

Unlike closed products the distinction is **technical, not commercial**
(§6.2): both paths are free, and neither is a degraded tier of the other.

**Two properties this must never violate**, because both fail silently:

1. *Conflation may drop intermediate values, never the latest one.* A
   display showing a superseded price is worse than a display showing
   nothing, because nothing is visibly missing and a stale number is not.
2. *TPIPE loses nothing, ever.* If the plant cannot deliver every tick it
   must say so rather than quietly thin the stream — a gap that is not
   reported is indistinguishable from a market that went quiet.

Transport (Redpanda for the durable log, NATS for fanout — §8.2) is
deliberately absent. This is the semantics, expressed as ordinary Python so
it can be tested exhaustively without a broker; the transports wrap it
rather than reimplement it.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from treble.core.identifiers import TUID


class Tick(BaseModel):
    """One normalised update for one instrument.

    Sequence numbers are per-instrument and strictly increasing: they are how
    a subscriber detects a gap. Wall-clock time cannot do that job, because
    two ticks can share a timestamp and clocks move backwards.
    """

    model_config = ConfigDict(frozen=True)

    subject: TUID
    field: str
    value: float
    #: Venue or plant sequence, per instrument, strictly increasing.
    sequence: int
    #: When the venue timestamped it, not when we received it.
    exchange_time: datetime
    #: Traded quantity, where the venue reports one. Optional because not
    #: every update is a trade — a quote or an index level has no size — and
    #: because a VWAP computed over ticks with an assumed size of 1 is a
    #: simple average wearing a volume-weighted name. `mktvwap` refuses
    #: rather than substituting one for the other.
    size: float | None = None


class GapDetectedError(RuntimeError):
    """A sequence number was skipped on the unconflated path.

    Raised rather than logged. A silently-thinned full-tick stream is a
    consumer computing a VWAP from prints it does not know are missing.
    """


class TickerPlant:
    """Current-state cache, a conflated view, and an unconflated stream.

    One instance holds the whole universe: the plant is replicated rather
    than sharded by client (§8.2), so failover is a reconnect rather than a
    data migration, and every node can answer for every instrument.
    """

    def __init__(self, *, tpipe_buffer: int = 1_000_000) -> None:
        #: Latest value per (subject, field) — the "initial paint" a new
        #: subscriber receives before any delta (§8.2).
        self._image: dict[tuple[TUID, str], Tick] = {}
        #: Highest sequence seen per instrument, for gap detection.
        self._sequence: dict[TUID, int] = {}
        #: Unconflated queue. Bounded, because unbounded means the plant
        #: dies of memory under load instead of reporting that it is behind.
        self._tpipe: deque[Tick] = deque(maxlen=tpipe_buffer)
        self._dropped = 0

    # -- ingest --------------------------------------------------------

    def publish(self, tick: Tick) -> None:
        """Accept one normalised tick.

        Out-of-order and duplicate ticks are rejected rather than applied: a
        late print overwriting a newer one would move the displayed price
        backwards in time, which no downstream check would catch.
        """
        last = self._sequence.get(tick.subject)
        if last is not None:
            if tick.sequence <= last:
                # Stale or duplicate. Dropping is correct — the image
                # already holds a later state for this instrument.
                return
            if tick.sequence != last + 1:
                raise GapDetectedError(
                    f"{tick.subject}: sequence jumped {last} -> {tick.sequence}; "
                    f"{tick.sequence - last - 1} update(s) lost upstream"
                )

        self._sequence[tick.subject] = tick.sequence
        self._image[(tick.subject, tick.field)] = tick

        if len(self._tpipe) == self._tpipe.maxlen:
            # The deque would silently evict the oldest. Counting it is what
            # lets TPIPE report a gap instead of pretending to be complete.
            self._dropped += 1
        self._tpipe.append(tick)

    # -- display path (conflated) --------------------------------------

    def image(self, subject: TUID, field: str) -> Tick | None:
        """The latest value: what a new display subscriber paints first."""
        return self._image.get((subject, field))

    def conflated(self) -> tuple[Tick, ...]:
        """One update per instrument-field — the latest of each.

        Conflation drops the superseded and keeps the current. That ordering
        guarantee is the property a display depends on and the one a naive
        "take every Nth tick" sampler would break.
        """
        return tuple(self._image[key] for key in sorted(self._image))

    # -- machine path (unconflated) ------------------------------------

    def tpipe(self) -> Iterator[Tick]:
        """Every tick, in arrival order, draining the buffer.

        Raises if the buffer overflowed rather than yielding a stream that
        looks complete. A consumer that asked for full tick and received
        most of it has no way to know.
        """
        if self._dropped:
            dropped, self._dropped = self._dropped, 0
            self._tpipe.clear()
            raise GapDetectedError(
                f"TPIPE buffer overflowed: {dropped} tick(s) evicted. The stream "
                "is not complete and is not being served as though it were."
            )
        while self._tpipe:
            yield self._tpipe.popleft()

    # -- introspection --------------------------------------------------

    @property
    def instruments(self) -> int:
        return len(self._sequence)

    @property
    def pending(self) -> int:
        return len(self._tpipe)
