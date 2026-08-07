"""Kafka/Redpanda transport (spec §13.2, §21, spec table §713).

The spec's durable log. Written against the Kafka wire protocol, which is
what Redpanda speaks too — Redpanda is a drop-in for the broker this is
tested against, and the reason the module is named for the protocol rather
than for either product.

**Why this exists when JetStream already retains.** The previous commit
shipped NATS carrying both spec roles and said plainly that nothing had
tested the partitioned multi-consumer case that is the reason the spec names
Kafka at all. That is this. Ticks are keyed by instrument, so every update
for one instrument lands on one partition and is therefore ordered with
respect to the others — a guarantee that survives many consumers reading in
parallel, which is the property a subject-fanout broker does not give.

**One real semantic difference, not hidden.** NATS filters server-side by
subject; Kafka has no such thing, so a per-instrument subscription here
reads the topic and discards what it did not ask for. That is more bytes
over the wire for the same answer, and it is why `subscribe(None)` is the
efficient call on this transport and `subscribe(one_instrument)` is not.
The conformance suite holds both to the same observable behaviour, which is
the point of the seam; it does not pretend the cost is the same.

**Ordering is per instrument, never global.** Kafka orders within a
partition, and with three partitions there is no total order across
instruments. Nothing here needs one — `sequence` is per-instrument, and
that is exactly what gap detection reads — but a caller that assumed a
global order would be wrong, so the topic is created with more than one
partition in tests rather than one, to stop that assumption from ever
appearing to hold.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from types import TracebackType

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

from treble.core.identifiers import TUID
from treble.plant.conflation import Tick
from treble.plant.transport import decode_tick, encode_tick

#: Default topic. One topic for the tape, partitioned by instrument, rather
#: than a topic per instrument: Kafka's per-topic overhead is real and a
#: topic count that tracks the security master is an operational problem
#: rather than a modelling one.
DEFAULT_TOPIC = "treble.ticks"

#: Partitions on a topic this creates. More than one deliberately — see the
#: module docstring on global ordering.
DEFAULT_PARTITIONS = 3

#: How long a read waits before deciding the tape has gone quiet.
DEFAULT_POLL_SECONDS = 1.0


class KafkaTickTransport:
    """A :class:`~treble.plant.transport.TickTransport` over Kafka/Redpanda."""

    def __init__(self, bootstrap: str, topic: str, producer: AIOKafkaProducer) -> None:
        self._bootstrap = bootstrap
        self._topic = topic
        self._producer = producer
        self._closed = False

    @property
    def bootstrap(self) -> str:
        """The broker this is connected to; read by tests that ask it
        directly rather than trusting this module's own constants."""
        return self._bootstrap

    @property
    def topic(self) -> str:
        """The topic in use."""
        return self._topic

    @classmethod
    async def connect(
        cls,
        bootstrap: str,
        *,
        topic: str = DEFAULT_TOPIC,
        partitions: int = DEFAULT_PARTITIONS,
    ) -> KafkaTickTransport:
        """Connect, ensuring the topic exists.

        Creating it here rather than relying on `auto.create.topics.enable`:
        an auto-created topic gets the broker's default partition count,
        which is one on a default install. A single partition would make the
        ordering test pass for a reason that has nothing to do with keying,
        and would quietly remove the parallelism this transport exists for.
        """
        admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap)
        await admin.start()
        try:
            # TopicAlreadyExistsError specifically, not Exception. The blanket
            # version was written first, and it is the same shape as the bug
            # in the NATS transport's connect(), where catching everything
            # turned "subjects overlap with an existing stream" into a
            # recovery path that then failed with an unrelated message.
            with contextlib.suppress(TopicAlreadyExistsError):
                await admin.create_topics(
                    [NewTopic(topic, num_partitions=partitions, replication_factor=1)]
                )
        finally:
            await admin.close()

        producer = AIOKafkaProducer(bootstrap_servers=bootstrap, acks="all")
        await producer.start()
        return cls(bootstrap, topic, producer)

    async def publish(self, tick: Tick) -> None:
        """Publish one tick, keyed by instrument, waiting for the ack.

        `acks="all"` and `send_and_wait`, not fire-and-forget. This is the
        durable log a replay reads; a publish that returns before the broker
        has the record would let the log lose exactly what replay is later
        expected to reproduce.

        The key is what puts one instrument on one partition, and so is what
        makes `sequence` mean anything on the far side.
        """
        await self._producer.send_and_wait(
            self._topic, encode_tick(tick), key=str(tick.subject).encode()
        )

    async def subscribe(
        self, subject: TUID | None = None, *, poll_seconds: float = DEFAULT_POLL_SECONDS
    ) -> AsyncIterator[Tick]:
        """Live ticks from the end of the log onward.

        Awaited, like every implementation of this protocol: the consumer
        must have been assigned its partitions before this returns, or a
        publish issued straight afterwards is not in the "live" window and
        never arrives.
        """
        return await self._iterate(subject, "latest", poll_seconds, stop_when_idle=False)

    async def replay(
        self, subject: TUID | None = None, *, poll_seconds: float = DEFAULT_POLL_SECONDS
    ) -> AsyncIterator[Tick]:
        """Every retained tick from the start of the log (I5)."""
        return await self._iterate(subject, "earliest", poll_seconds, stop_when_idle=True)

    async def _iterate(
        self,
        subject: TUID | None,
        offset_reset: str,
        poll_seconds: float,
        *,
        stop_when_idle: bool,
    ) -> AsyncIterator[Tick]:
        consumer = AIOKafkaConsumer(
            self._topic,
            bootstrap_servers=self._bootstrap,
            auto_offset_reset=offset_reset,
            enable_auto_commit=False,
            # No group: every subscriber reads the whole topic. A shared
            # group would split partitions between them, so two screens
            # watching the same instrument would each see part of the tape —
            # which is the fanout bug, wearing a consumer-group hat.
            group_id=None,
        )
        await consumer.start()
        if offset_reset == "latest":
            # Position at the end *before* returning. A live subscriber that
            # is still being assigned partitions when the caller publishes
            # misses those ticks, which is the same defect that made the
            # NATS subscribe() an awaited call rather than a lazy generator.
            await consumer.seek_to_end()
        return self._pump(consumer, subject, poll_seconds, stop_when_idle=stop_when_idle)

    async def _pump(
        self,
        consumer: AIOKafkaConsumer,
        subject: TUID | None,
        poll_seconds: float,
        *,
        stop_when_idle: bool,
    ) -> AsyncIterator[Tick]:
        try:
            while True:
                batches = await consumer.getmany(timeout_ms=int(poll_seconds * 1000))
                if not batches:
                    if stop_when_idle:
                        return
                    continue
                for records in batches.values():
                    for record in records:
                        # Client-side filtering: Kafka has no server-side
                        # subject filter, so the discarded bytes crossed the
                        # wire. Stated in the module docstring rather than
                        # hidden behind an interface that looks symmetric.
                        #
                        # Filtered on the *decoded* subject, not on the record
                        # key. The key is a routing decision — it picks the
                        # partition — and reading it as the identity too made
                        # the two inseparable: removing the key broke delivery
                        # entirely, so the ordering test failed with an empty
                        # list and appeared to prove a partitioning property
                        # it had not touched.
                        tick = decode_tick(record.value)
                        if subject is not None and tick.subject != subject:
                            continue
                        yield tick
        finally:
            with contextlib.suppress(Exception):
                await consumer.stop()

    async def close(self) -> None:
        """Flush and disconnect. Safe to call twice."""
        if not self._closed:
            self._closed = True
            with contextlib.suppress(asyncio.CancelledError):
                await self._producer.stop()

    async def __aenter__(self) -> KafkaTickTransport:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()


__all__ = [
    "DEFAULT_PARTITIONS",
    "DEFAULT_POLL_SECONDS",
    "DEFAULT_TOPIC",
    "KafkaTickTransport",
]
