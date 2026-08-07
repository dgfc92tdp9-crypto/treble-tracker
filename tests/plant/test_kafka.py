"""What Kafka adds over subject fanout (spec §713, I5).

The conformance suite checks what every transport must do, and JetStream
already gave durable replay. This checks the one thing that is the actual
reason the spec names Kafka: **keyed partitioning**, and the per-instrument
ordering that survives many consumers reading the tape in parallel.

The previous commit shipped NATS in both spec roles and recorded that this
case was untested. It is what these tests are.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from aiokafka.admin import AIOKafkaAdminClient

from treble.core.identifiers import TUID
from treble.plant.conflation import GapDetectedError, Tick, TickerPlant
from treble.plant.kafka import DEFAULT_PARTITIONS, KafkaTickTransport

BTC = TUID("coinbase:BTC-USD")
ETH = TUID("coinbase:ETH-USD")


def _tick(sequence: int, value: float, *, subject: TUID = BTC) -> Tick:
    return Tick(
        subject=subject,
        field="LAST_PRICE",
        value=value,
        sequence=sequence,
        exchange_time=datetime(2026, 8, 7, 9, 0, 0, tzinfo=UTC),
    )


async def _drain(stream: AsyncIterator[Tick], timeout: float = 40.0) -> list[Tick]:
    out: list[Tick] = []

    async def gather() -> None:
        async for tick in stream:
            out.append(tick)

    await asyncio.wait_for(gather(), timeout)
    return out


@pytest.fixture
async def transport(
    request: pytest.FixtureRequest, kafka_bootstrap: str
) -> AsyncIterator[KafkaTickTransport]:
    safe = request.node.name.replace("[", ".").replace("]", "")
    made = await KafkaTickTransport.connect(kafka_bootstrap, topic=f"kt.{safe}")
    try:
        yield made
    finally:
        await made.close()


class TestKeyedPartitioning:
    async def test_one_instrument_keeps_its_order_across_partitions(
        self, transport: KafkaTickTransport
    ) -> None:
        """The guarantee the spec wants Kafka for.

        Kafka orders within a partition, not across the topic. Keying by
        instrument is what puts every update for one instrument on one
        partition, and so is what makes `sequence` mean anything on the far
        side. Interleaving a second instrument is deliberate: without the
        key, these would be spread across partitions and the order of either
        one would not be guaranteed.
        """
        for n in range(1, 13):
            await transport.publish(_tick(n, 64000.0 + n, subject=BTC))
            await transport.publish(_tick(n, 3000.0 + n, subject=ETH))
        replayed = await _drain(await transport.replay(BTC))
        assert [t.sequence for t in replayed] == list(range(1, 13))

    async def test_the_topic_really_has_more_than_one_partition(
        self, transport: KafkaTickTransport
    ) -> None:
        """Otherwise the ordering test above passes for the wrong reason.

        A single-partition topic orders everything globally, so the keying
        could be entirely broken and nothing would notice. This is the check
        that stops the one above from being a check that cannot fail.
        """
        assert DEFAULT_PARTITIONS > 1
        # Ask the broker, not the constant. The first version of this test
        # asserted only that the default was greater than one, which is true
        # of a module-level integer whatever the topic actually looks like.
        admin = AIOKafkaAdminClient(bootstrap_servers=transport.bootstrap)
        await admin.start()
        try:
            described = await admin.describe_topics([transport.topic])
        finally:
            await admin.close()
        assert len(described[0]["partitions"]) == DEFAULT_PARTITIONS

    async def test_two_consumers_each_see_the_whole_tape(
        self, transport: KafkaTickTransport
    ) -> None:
        """Fanout, which is the half NATS was already doing.

        Both read independently rather than sharing partitions. A consumer
        group would split the tape between them, so two screens watching one
        instrument would each get part of it — the fanout bug wearing a
        consumer-group hat, which is why `group_id=None`.
        """
        for n in range(1, 5):
            await transport.publish(_tick(n, 100.0 + n))
        first, second = await asyncio.gather(
            _drain(await transport.replay(BTC)),
            _drain(await transport.replay(BTC)),
        )
        assert [t.sequence for t in first] == [1, 2, 3, 4]
        assert [t.sequence for t in second] == [1, 2, 3, 4]


class TestTheLogIsDurable:
    async def test_replay_returns_ticks_published_before_anyone_subscribed(
        self, transport: KafkaTickTransport
    ) -> None:
        for n in range(1, 4):
            await transport.publish(_tick(n, 64000.0 + n))
        assert [t.sequence for t in await _drain(await transport.replay(BTC))] == [1, 2, 3]

    async def test_a_replayed_tape_still_detects_its_own_gaps(
        self, transport: KafkaTickTransport
    ) -> None:
        for n in (1, 2, 4):
            await transport.publish(_tick(n, 100.0 + n))
        plant = TickerPlant()
        errors = []
        for tick in await _drain(await transport.replay(BTC)):
            try:
                plant.publish(tick)
            except GapDetectedError as exc:
                errors.append(str(exc))
        assert errors, "the replayed tape lost its sequence numbers"
        assert "2 -> 4" in errors[0]
