"""One suite, every transport (spec §8.1, §13.2).

The same discipline `render/` uses for renderers, for the same reason. A
transport that is only exercised in-process has never had its wire format
tested, and the symptom is not an exception — it is a tick that arrives
subtly wrong, in a decimal place nobody reads until a VWAP disagrees with a
venue's.

So every implementation runs the identical assertions, and the NATS one runs
them against a real `nats-server` subprocess speaking the real protocol.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from treble.core.identifiers import TUID
from treble.plant.conflation import Tick
from treble.plant.kafka import KafkaTickTransport
from treble.plant.natsjs import NatsTickTransport
from treble.plant.transport import (
    InProcessTransport,
    TickTransport,
    decode_tick,
    decode_token,
    encode_tick,
    encode_token,
    subject_for,
)

pytestmark = pytest.mark.asyncio

BTC = TUID("coinbase:BTC-USD")
ETH = TUID("coinbase:ETH-USD")


def _tick(sequence: int, value: float, *, subject: TUID = BTC, size: float | None = None) -> Tick:
    return Tick(
        subject=subject,
        field="LAST_PRICE",
        value=value,
        sequence=sequence,
        exchange_time=datetime(2026, 8, 6, 12, 30, 15, 123456, tzinfo=UTC),
        size=size,
    )


async def _take(stream: AsyncIterator[Tick], count: int, timeout: float = 15.0) -> list[Tick]:
    """Read `count` ticks, or fail.

    Bounded deliberately. The first run of this suite hung for ten minutes
    on a subscription that never registered; an unbounded read turns a
    delivery bug into a stalled CI job instead of a failing test, and the
    bug it was hiding was real.
    """
    out: list[Tick] = []

    async def gather() -> None:
        async for tick in stream:
            out.append(tick)
            if len(out) == count:
                return

    await asyncio.wait_for(gather(), timeout)
    return out


# --------------------------------------------------------------------------
# The codec, which every transport shares and which nothing else guards.
# --------------------------------------------------------------------------


class TestTheCodecIsExact:
    async def test_a_tick_survives_the_round_trip_unchanged(self) -> None:
        """Not "close". A price that shifts in the last decimal between two
        processes is the worst defect this layer can have and the one least
        likely to be noticed."""
        original = _tick(7, 64000.123456789, size=1.3e-07)
        assert decode_tick(encode_tick(original)) == original

    @pytest.mark.parametrize(
        "value",
        [0.1 + 0.2, 1e-17, 1.7976931348623157e308, 64000.123456789012, -0.0],
    )
    async def test_awkward_floats_survive_exactly(self, value: float) -> None:
        restored = decode_tick(encode_tick(_tick(1, value)))
        assert restored.value == value

    async def test_an_absent_size_stays_absent(self) -> None:
        """`None` and `0.0` mean different things — no size reported against
        a zero-size trade — and JSON is happy to blur them."""
        assert decode_tick(encode_tick(_tick(1, 10.0, size=None))).size is None
        assert decode_tick(encode_tick(_tick(1, 10.0, size=0.0))).size == 0.0


class TestSubjectEncoding:
    """Measured against a live server: a raw dotted id published as
    `ticks.has.dot` is accepted, retained, and never delivered to a
    subscriber on `ticks.*` — silent loss, not an error.

    These are the tests that fail when the escaping is removed. Verified by
    doing it: `test_a_dotted_id_stays_one_subject_token` and the `.` case of
    `test_no_reserved_character_survives_into_a_subject` both fail, and
    nothing else does."""

    @pytest.mark.parametrize(
        "raw",
        ["coinbase:BTC-USD", "a.b", "a*b", "a>b", "a b", "100%", "a%2Eb", "swaption:EUR:2Y10Y"],
    )
    async def test_every_token_round_trips(self, raw: str) -> None:
        assert decode_token(encode_token(raw)) == raw

    @pytest.mark.parametrize("reserved", [".", "*", ">", " "])
    async def test_no_reserved_character_survives_into_a_subject(self, reserved: str) -> None:
        """The bug was that `quote(safe="")` leaves `.` alone whatever you
        pass it, so this asserts the property rather than the call."""
        assert reserved not in encode_token(f"a{reserved}b")

    async def test_a_dotted_id_stays_one_subject_token(self) -> None:
        assert subject_for(TUID("has.dot")).count(".") == 1


# --------------------------------------------------------------------------
# The conformance suite: identical for every implementation.
# --------------------------------------------------------------------------


@pytest.fixture(params=["in-process", "nats", "kafka"])
async def transport(
    request: pytest.FixtureRequest, nats_url: str, kafka_bootstrap: str
) -> AsyncIterator[TickTransport]:
    if request.param == "in-process":
        made: TickTransport = InProcessTransport()
    elif request.param == "kafka":
        # A fresh topic per test. Kafka has no server-side subject filter, so
        # a shared topic would leave `subscribe(None)` reading every earlier
        # test's ticks, and the wildcard test asserts an exact set.
        safe = request.node.name.replace("[", ".").replace("]", "")
        made = await KafkaTickTransport.connect(kafka_bootstrap, topic=f"conf.{safe}")
    else:
        # One stream, not one per test: JetStream refuses two streams whose
        # subjects overlap, and every transport here publishes under
        # `ticks.>` by design. Isolation comes from DeliverPolicy.NEW, which
        # delivers only what arrives after the subscription exists.
        made = await NatsTickTransport.connect(nats_url)
    try:
        yield made
    finally:
        await made.close()


class TestEveryTransport:
    async def test_a_published_tick_reaches_a_subscriber_unchanged(
        self, transport: TickTransport
    ) -> None:
        stream = await transport.subscribe(BTC)
        sent = _tick(1, 64000.5, size=0.25)
        await transport.publish(sent)
        assert await _take(stream, 1) == [sent]

    async def test_order_is_preserved_per_instrument(self, transport: TickTransport) -> None:
        """Sequence numbers are how a subscriber detects a gap, so a
        transport that reorders makes gap detection report false losses."""
        stream = await transport.subscribe(BTC)
        for n in range(1, 6):
            await transport.publish(_tick(n, 64000.0 + n))
        assert [t.sequence for t in await _take(stream, 5)] == [1, 2, 3, 4, 5]

    async def test_a_subscriber_to_one_instrument_gets_only_that_one(
        self, transport: TickTransport
    ) -> None:
        stream = await transport.subscribe(BTC)
        await transport.publish(_tick(1, 3000.0, subject=ETH))
        await transport.publish(_tick(1, 64000.0, subject=BTC))
        assert [t.subject for t in await _take(stream, 1)] == [BTC]

    async def test_a_dotted_instrument_id_is_delivered(self, transport: TickTransport) -> None:
        """A dotted id survives the whole path.

        Kept, but **not** a regression guard for the dot-escaping, and
        labelled that way because it first claimed to be one. Removing the
        escaping leaves this passing: publish and subscribe both build the
        same exact subject, and the all-instruments pattern is `ticks.>`,
        which matches any depth. The escaping protects a single-level
        wildcard that this design does not yet use, so the checks that can
        actually fail are the property ones in `TestSubjectEncoding`."""
        dotted = TUID("venue:BRK.B")
        stream = await transport.subscribe(dotted)
        sent = _tick(1, 712.34, subject=dotted)
        await transport.publish(sent)
        assert await _take(stream, 1) == [sent]

    async def test_a_wildcard_subscriber_sees_every_instrument(
        self, transport: TickTransport
    ) -> None:
        stream = await transport.subscribe(None)
        await transport.publish(_tick(1, 64000.0, subject=BTC))
        await transport.publish(_tick(1, 3000.0, subject=ETH))
        assert {t.subject for t in await _take(stream, 2)} == {BTC, ETH}

    async def test_close_is_safe_twice(self, transport: TickTransport) -> None:
        await transport.close()
        await transport.close()
