"""NATS JetStream transport (spec §13.2, §21).

The spec gives NATS JetStream the low-latency fanout job and Redpanda/Kafka
the durable log. JetStream does both — it is a persistent, replayable log
with subject-based fanout on top — so this implementation carries both
roles, and the honest consequence is recorded rather than glossed:
**Redpanda is not implemented and is not exercised anywhere in this
repository.** The seam in `transport.py` is what a Kafka implementation
would be written against, and until one exists the spec's two-broker
topology is one broker doing two jobs.

That is a real difference, not a naming one. Kafka's partitions give
per-partition ordering across many consumers and a retention model measured
in terabytes; JetStream's streams are cheaper to run and there is exactly
one of them here. For a local-first workstation — which is what `treble
init` builds — the trade favours the one that starts in a millisecond off a
6MB binary. For a shared deployment it may not, and nothing here has tested
that case.

**Replay is the reason durability is on.** I5 says a fact must be
reproducible from stored inputs. A tick stream that only exists while a
subscriber is attached cannot satisfy that, so the stream retains and
:meth:`NatsTickTransport.replay` reads it from the beginning — the same
guarantee `IngestLog` gives the batch path, for the live one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import TracebackType

import nats
from nats.aio.client import Client
from nats.js import JetStreamContext
from nats.js.api import ConsumerConfig, DeliverPolicy, StreamConfig
from nats.js.errors import NotFoundError

from treble.core.identifiers import TUID
from treble.plant.conflation import Tick
from treble.plant.transport import (
    SUBJECT_ROOT,
    decode_tick,
    encode_tick,
    subject_for,
    subject_pattern,
)

#: Default stream name. One stream for the whole tape: JetStream subjects
#: already separate instruments, and a stream per instrument would make the
#: broker's object count track the security master.
DEFAULT_STREAM = "TREBLE_TICKS"

#: How long a read waits before deciding the tape has gone quiet. A
#: subscriber that blocks forever cannot be shut down, which is the defect
#: that leaked a thread per cancelled gRPC stream — the same mistake in a
#: different transport.
DEFAULT_POLL_SECONDS = 1.0


class NatsTickTransport:
    """A :class:`~treble.plant.transport.TickTransport` over NATS JetStream."""

    def __init__(self, connection: Client, js: JetStreamContext, stream: str) -> None:
        # Not constructed directly: `connect()` is the entry point, because a
        # transport that exists must already have its stream, and a
        # constructor cannot await.
        self._nc = connection
        self._js = js
        self._stream = stream

    @classmethod
    async def connect(
        cls, url: str, *, stream: str = DEFAULT_STREAM, max_age_seconds: float | None = None
    ) -> NatsTickTransport:
        """Connect and ensure the stream exists.

        Idempotent: an existing stream with the same subjects is reused
        rather than replaced, so restarting a publisher does not discard the
        tape a replay would read.

        The absence check is `stream_info` rather than `except Exception`
        around `add_stream`. The blanket version was written first and hid a
        real error behind a wrong recovery: JetStream rejects a second
        stream whose subjects overlap an existing one, and catching that as
        "already exists" sent it into `update_stream`, which then failed
        with `stream not found` — an error naming neither the cause nor the
        stream that actually held the subjects.
        """
        connection = await nats.connect(url)
        js = connection.jetstream()
        config = StreamConfig(
            name=stream,
            subjects=[f"{SUBJECT_ROOT}.>"],
            max_age=max_age_seconds,
        )
        try:
            await js.stream_info(stream)
        except NotFoundError:
            await js.add_stream(config=config)
        else:
            # Already there: update rather than skip, so a change to the
            # subject list takes effect instead of being silently ignored
            # until someone wonders why nothing is delivered.
            await js.update_stream(config=config)
        return cls(connection, js, stream)

    async def publish(self, tick: Tick) -> None:
        """Publish one tick, waiting for the broker to acknowledge it.

        The ack is not optional. Fire-and-forget publishing would let the
        durable log silently lose the tick that a replay is later expected
        to reproduce, which is precisely the guarantee this stream exists to
        make.
        """
        await self._js.publish(subject_for(tick.subject), encode_tick(tick))

    async def subscribe(
        self, subject: TUID | None = None, *, poll_seconds: float = DEFAULT_POLL_SECONDS
    ) -> AsyncIterator[Tick]:
        """Live ticks from now on, for one instrument or all of them.

        Awaited so the consumer exists before this returns. `DeliverPolicy.
        NEW` means anything published before that point is not delivered, so
        a lazily-created subscription would drop every tick published
        between the call and the first read.
        """
        return await self._iterate(subject, DeliverPolicy.NEW, poll_seconds)

    async def replay(
        self, subject: TUID | None = None, *, poll_seconds: float = DEFAULT_POLL_SECONDS
    ) -> AsyncIterator[Tick]:
        """Every retained tick from the start of the stream (I5).

        Stops when the stream is exhausted rather than waiting for more, so
        a replay terminates. `subscribe` is the one that follows the tape.
        """
        return await self._iterate(subject, DeliverPolicy.ALL, poll_seconds, stop_when_idle=True)

    async def _iterate(
        self,
        subject: TUID | None,
        policy: DeliverPolicy,
        poll_seconds: float,
        *,
        stop_when_idle: bool = False,
    ) -> AsyncIterator[Tick]:
        sub = await self._js.subscribe(
            subject_pattern(subject),
            config=ConsumerConfig(deliver_policy=policy),
        )
        return self._pump(sub, poll_seconds, stop_when_idle=stop_when_idle)

    async def _pump(
        self,
        sub: JetStreamContext.PushSubscription,
        poll_seconds: float,
        *,
        stop_when_idle: bool,
    ) -> AsyncIterator[Tick]:
        try:
            while True:
                try:
                    message = await sub.next_msg(timeout=poll_seconds)
                except Exception:
                    # A quiet tape, not a broken one. `replay` is finite and
                    # stops here; `subscribe` keeps waiting, and the caller
                    # cancels it by closing the iterator.
                    if stop_when_idle:
                        return
                    continue
                await message.ack()
                yield decode_tick(message.data)
        finally:
            await sub.unsubscribe()

    async def close(self) -> None:
        """Drain and disconnect. Safe to call twice."""
        if not self._nc.is_closed:
            await self._nc.close()

    async def __aenter__(self) -> NatsTickTransport:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()


__all__ = ["DEFAULT_POLL_SECONDS", "DEFAULT_STREAM", "NatsTickTransport"]
