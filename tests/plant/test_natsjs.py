"""What JetStream adds over in-process fanout (spec §13.2, I5).

The conformance suite checks what every transport must do. This checks the
thing only a broker with a durable log can do: hand back the tape after the
subscriber that missed it has gone away.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from treble.core.identifiers import TUID
from treble.plant.conflation import GapDetectedError, Tick, TickerPlant
from treble.plant.natsjs import NatsTickTransport

BTC = TUID("coinbase:BTC-USD")


def _tick(sequence: int, value: float, *, subject: TUID = BTC) -> Tick:
    return Tick(
        subject=subject,
        field="LAST_PRICE",
        value=value,
        sequence=sequence,
        exchange_time=datetime(2026, 8, 6, 12, 30, 15, tzinfo=UTC),
    )


async def _drain(stream: AsyncIterator[Tick], timeout: float = 20.0) -> list[Tick]:
    out: list[Tick] = []

    async def gather() -> None:
        async for tick in stream:
            out.append(tick)

    await asyncio.wait_for(gather(), timeout)
    return out


@pytest.fixture
async def transport(nats_url: str) -> AsyncIterator[NatsTickTransport]:
    # The default stream, not a second one. JetStream rejects two streams
    # whose subjects overlap, and every transport here publishes under
    # `ticks.>`; a separate stream name passed alone and failed the moment
    # this file ran alongside the conformance suite. Isolation comes from
    # each test owning an instrument id, which is what `replay` filters on.
    made = await NatsTickTransport.connect(nats_url)
    try:
        yield made
    finally:
        await made.close()


def test_the_broker_is_real(nats_url: str) -> None:
    """The fixture starts a process and this connects to it.

    Cheap, and worth having: every other test in this file would also pass
    against a fixture that quietly returned an in-process fake, and the
    entire reason for downloading a 6MB binary is that they should not.
    """
    host, port = nats_url.removeprefix("nats://").split(":")
    with socket.create_connection((host, int(port)), timeout=5) as sock:
        # NATS servers greet with an INFO line before the client says a word.
        assert sock.recv(64).startswith(b"INFO ")


class TestTheLogIsDurable:
    async def test_replay_returns_ticks_published_before_anyone_subscribed(
        self, transport: NatsTickTransport
    ) -> None:
        """I5 for the live path. A tape that exists only while a subscriber
        is attached cannot reproduce anything."""
        own = TUID("replay:before-subscribe")
        for n in range(1, 4):
            await transport.publish(_tick(n, 64000.0 + n, subject=own))
        replayed = await _drain(await transport.replay(own))
        assert [t.sequence for t in replayed] == [1, 2, 3]

    async def test_replay_terminates_rather_than_waiting_for_more(
        self, transport: NatsTickTransport
    ) -> None:
        """`subscribe` follows the tape; `replay` is finite. If replay
        blocked at the end of the stream it could never be used to rebuild
        anything, because it would never return."""
        own = TUID("replay:terminates")
        await transport.publish(_tick(9, 1.0, subject=own))
        assert len(await _drain(await transport.replay(own), timeout=20.0)) >= 1

    async def test_a_replayed_tape_still_detects_its_own_gaps(
        self, transport: NatsTickTransport
    ) -> None:
        """The point of carrying `sequence` over the wire.

        A transport that dropped or reordered would make the plant's gap
        detector report losses that never happened — or, worse, not report
        ones that did.
        """
        gappy = TUID("replay:gappy")
        for n in (1, 2, 4):
            await transport.publish(_tick(n, 100.0 + n, subject=gappy))
        plant = TickerPlant()
        errors = []
        for tick in await _drain(await transport.replay(gappy)):
            # Only GapDetectedError. The first version caught Exception and
            # so turned a misspelled method name into a plausible-looking
            # error string that the assertion then inspected — a test that
            # would have passed for a reason unrelated to what it checks.
            try:
                plant.publish(tick)
            except GapDetectedError as exc:
                errors.append(str(exc))
        assert errors, "the replayed tape lost its sequence numbers"
        assert "2 -> 4" in errors[0]
