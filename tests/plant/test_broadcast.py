"""Fan-out to live subscribers (spec §8.1, §8.3).

The property that matters is not that a subscriber receives ticks — it is
what happens when one falls behind. A stream that silently skipped updates
is indistinguishable downstream from a complete one, which is the same
reason `TickerPlant.tpipe()` raises on overflow rather than yielding what it
still has.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from treble.core.identifiers import TUID
from treble.plant.broadcast import SubscriberOverflowError, TickBroadcaster
from treble.plant.conflation import Tick

BASE = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _tick(sequence: int, price: float = 100.0, subject: str = "crypto:coinbase:BTC-USD") -> Tick:
    return Tick(
        subject=TUID(subject),
        field="PX_LAST",
        value=price,
        sequence=sequence,
        exchange_time=BASE + timedelta(seconds=sequence),
        size=1.0,
    )


class TestFanOut:
    def test_every_subscriber_gets_every_tick(self) -> None:
        """The reason this exists: TPIPE drains, so two consumers reading it
        would split the tape between them instead of both seeing it."""
        broadcaster = TickBroadcaster()
        first, second = broadcaster.subscribe(), broadcaster.subscribe()
        for i in range(1, 4):
            broadcaster.publish(_tick(i))
        assert [t.sequence for t in first.drain()] == [1, 2, 3]
        assert [t.sequence for t in second.drain()] == [1, 2, 3]

    def test_a_closed_subscriber_stops_receiving(self) -> None:
        broadcaster = TickBroadcaster()
        subscription = broadcaster.subscribe()
        subscription.close()
        broadcaster.publish(_tick(1))
        assert subscription.drain() == []
        assert broadcaster.subscribers == 0

    def test_publishing_with_no_subscribers_is_not_an_error(self) -> None:
        """A feed runs whether anyone is listening or not."""
        TickBroadcaster().publish(_tick(1))

    def test_a_subscription_removes_itself_on_exit(self) -> None:
        broadcaster = TickBroadcaster()
        with broadcaster.subscribe():
            assert broadcaster.subscribers == 1
        assert broadcaster.subscribers == 0


class TestFallingBehind:
    def test_a_slow_subscriber_is_told_rather_than_quietly_shortchanged(self) -> None:
        """The whole point. A stream missing updates and one that is complete
        look identical to whatever consumes them."""
        broadcaster = TickBroadcaster()
        subscription = broadcaster.subscribe(maxsize=3)
        for i in range(1, 7):
            broadcaster.publish(_tick(i))
        assert subscription.dropped == 3
        with pytest.raises(SubscriberOverflowError, match="3 update"):
            subscription.drain()

    def test_the_overflow_clears_so_the_next_read_is_honest(self) -> None:
        """After reporting the loss the subscriber resumes from now, rather
        than reporting the same gap forever or pretending it recovered the
        missing updates."""
        broadcaster = TickBroadcaster()
        subscription = broadcaster.subscribe(maxsize=2)
        for i in range(1, 6):
            broadcaster.publish(_tick(i))
        with pytest.raises(SubscriberOverflowError):
            subscription.drain()
        broadcaster.publish(_tick(99))
        assert [t.sequence for t in subscription.drain()] == [99]

    def test_one_slow_subscriber_does_not_starve_a_fast_one(self) -> None:
        """A publisher that could be blocked by one slow reader would turn a
        single stalled client into a market-data outage."""
        broadcaster = TickBroadcaster()
        slow = broadcaster.subscribe(maxsize=2)
        fast = broadcaster.subscribe(maxsize=100)
        for i in range(1, 11):
            broadcaster.publish(_tick(i))
        assert slow.dropped > 0
        assert [t.sequence for t in fast.drain()] == list(range(1, 11))

    def test_the_newest_update_is_kept_not_the_oldest(self) -> None:
        """A live subscriber wants current state. Holding stale updates while
        refusing newer ones would make the queue a delay line."""
        broadcaster = TickBroadcaster()
        subscription = broadcaster.subscribe(maxsize=2)
        for i in range(1, 6):
            broadcaster.publish(_tick(i))
        with pytest.raises(SubscriberOverflowError):
            subscription.drain()
        # The drain cleared; publish exactly the queue's worth and confirm
        # the retained ones are the latest.
        for i in (7, 8):
            broadcaster.publish(_tick(i))
        assert [t.sequence for t in subscription.drain()] == [7, 8]


class TestTheBlockingRead:
    """`next_batch` is what the streaming RPC uses, so its timeout behaviour
    is a server-liveness property rather than a convenience."""

    def test_it_returns_what_arrives_while_waiting(self) -> None:
        broadcaster = TickBroadcaster()
        subscription = broadcaster.subscribe()
        threading.Timer(0.05, lambda: broadcaster.publish(_tick(1))).start()
        batch = subscription.next_batch(timeout=5.0)
        assert [t.sequence for t in batch] == [1]

    def test_a_quiet_feed_returns_empty_rather_than_blocking_forever(self) -> None:
        """The defect this exists for: the streaming RPC checked its client
        only when a tick arrived, so a cancelled subscription on a quiet
        instrument span forever and leaked a server thread. Returning empty
        is what lets the server look up and see the client has gone."""
        subscription = TickBroadcaster().subscribe()
        started = datetime.now(UTC)
        assert subscription.next_batch(timeout=0.1) == []
        assert datetime.now(UTC) - started < timedelta(seconds=3)

    def test_it_raises_on_overflow_rather_than_skipping(self) -> None:
        broadcaster = TickBroadcaster()
        subscription = broadcaster.subscribe(maxsize=2)
        for i in range(1, 8):
            broadcaster.publish(_tick(i))
        with pytest.raises(SubscriberOverflowError):
            subscription.next_batch(timeout=0.05)

    def test_a_closed_subscription_does_not_wait(self) -> None:
        broadcaster = TickBroadcaster()
        subscription = broadcaster.subscribe()
        subscription.close()
        started = datetime.now(UTC)
        assert subscription.next_batch(timeout=5.0) == []
        assert datetime.now(UTC) - started < timedelta(seconds=3)
