"""Fan-out for live subscribers (spec §8.1, §8.3).

`TickerPlant.tpipe()` drains: it is the machine path and has exactly one
consumer. A streaming RPC reading it would consume the tape out from under
whatever else was reading, and a second subscriber would get an empty stream
rather than the same one. `TickHistory` solved that for aggregation by
recording alongside; this solves it for *live* subscribers.

Every subscriber gets its own bounded queue. Two properties follow, and both
are the point:

**A slow subscriber cannot slow the feed.** Publishing never blocks. A
consumer that stops reading fills its own queue and nobody else notices,
which is the only arrangement under which one stalled client does not become
a market-data outage.

**A slow subscriber is told it fell behind.** When its queue is full the
oldest update is dropped and the loss is counted, and the next read raises
rather than yielding a stream that looks complete. This mirrors TPIPE's
overflow exactly: a consumer that asked for every tick and received most of
them has no way to know, and "most" is indistinguishable from "all" on a
screen.

Dropping the *oldest* rather than refusing the newest is deliberate. A live
subscriber wants the current state; holding stale updates while newer ones
are refused would make the queue a delay line that grows without bound in
usefulness terms.
"""

from __future__ import annotations

import threading
from collections import deque
from types import TracebackType

from treble.plant.conflation import Tick

#: How many updates a subscriber may fall behind before losing the oldest.
#: Generous, because the cost of a large queue is memory and the cost of a
#: small one is a false overflow on an ordinary garbage-collection pause.
DEFAULT_QUEUE = 10_000


class SubscriberOverflowError(RuntimeError):
    """This subscriber fell behind and updates were dropped.

    Raised on read rather than at the moment of the drop, because the drop
    happens on the publisher's thread and a publisher must never be made to
    fail by one slow reader.
    """


class Subscription:
    """One subscriber's view of the tape.

    Not constructed directly: `TickBroadcaster.subscribe()` returns one and
    registers it, so a subscription that exists is always one the broadcaster
    knows to feed.
    """

    def __init__(self, broadcaster: TickBroadcaster, *, maxsize: int = DEFAULT_QUEUE) -> None:
        self._broadcaster = broadcaster
        self._queue: deque[Tick] = deque(maxlen=maxsize)
        self._dropped = 0
        self._closed = False
        self._lock = threading.Lock()
        self._arrived = threading.Condition(self._lock)

    # -- publisher side (never blocks) ---------------------------------

    def _offer(self, tick: Tick) -> None:
        with self._arrived:
            if self._closed:
                return
            if len(self._queue) == self._queue.maxlen:
                # The deque would evict silently. Counting it is what lets
                # the next read say the stream is incomplete.
                self._dropped += 1
            self._queue.append(tick)
            self._arrived.notify()

    # -- subscriber side ------------------------------------------------

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    def drain(self) -> list[Tick]:
        """Everything queued now, without waiting.

        Raises if anything was lost, for the same reason `tpipe()` does: a
        partial stream served as a whole one cannot be detected downstream.
        """
        with self._lock:
            if self._dropped:
                dropped, self._dropped = self._dropped, 0
                self._queue.clear()
                raise SubscriberOverflowError(
                    f"subscriber fell behind: {dropped} update(s) dropped. The stream is "
                    "not complete and is not being served as though it were"
                )
            out = list(self._queue)
            self._queue.clear()
            return out

    def next_batch(self, *, timeout: float) -> list[Tick]:
        """Everything queued, waiting up to `timeout` for the first arrival.

        Returns an empty list when nothing arrived in time — *not* an error.
        A caller needs that to regain control on a quiet feed: a server
        streaming this to a client must be able to notice the client went
        away, and an instrument that is simply not trading must not look
        like a hung connection.

        Raises on overflow, like `drain`.
        """
        with self._arrived:
            if not self._queue and not self._closed and not self._dropped:
                self._arrived.wait(timeout=timeout)
            if self._dropped:
                dropped, self._dropped = self._dropped, 0
                self._queue.clear()
                raise SubscriberOverflowError(
                    f"subscriber fell behind: {dropped} update(s) dropped. The stream is "
                    "not complete and is not being served as though it were"
                )
            out = list(self._queue)
            self._queue.clear()
            return out

    def close(self) -> None:
        with self._arrived:
            self._closed = True
            self._arrived.notify_all()
        self._broadcaster._remove(self)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def __enter__(self) -> Subscription:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class TickBroadcaster:
    """Fans every published tick out to every live subscriber."""

    def __init__(self) -> None:
        self._subscribers: list[Subscription] = []
        self._lock = threading.Lock()

    def subscribe(self, *, maxsize: int = DEFAULT_QUEUE) -> Subscription:
        subscription = Subscription(self, maxsize=maxsize)
        with self._lock:
            self._subscribers.append(subscription)
        return subscription

    def _remove(self, subscription: Subscription) -> None:
        with self._lock:
            if subscription in self._subscribers:
                self._subscribers.remove(subscription)

    def publish(self, tick: Tick) -> None:
        """Offer to every subscriber. Never blocks and never raises.

        A publisher that could be made to fail by one slow reader would turn
        a single stalled client into a market-data outage.
        """
        with self._lock:
            targets = list(self._subscribers)
        for subscription in targets:
            subscription._offer(tick)

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subscribers)


__all__ = [
    "DEFAULT_QUEUE",
    "SubscriberOverflowError",
    "Subscription",
    "TickBroadcaster",
]
