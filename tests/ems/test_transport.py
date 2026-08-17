"""FIX over a loopback socket (P3_3).

Framing is the whole job and the part a hand-off test cannot exercise. TCP
is a byte stream with no message boundaries: a read returns whatever
arrived — half a message, three messages, one split across packets — and a
receiver assuming one read is one message works perfectly on loopback with
small messages and fails the first time a venue sends quickly.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from treble.ems.session import Session
from treble.ems.simulator import EXECUTION_REPORT, Simulator, new_order_single
from treble.ems.transport import HOST, read_messages, running_simulator

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_a_session_logs_on_and_trades_over_a_socket() -> None:
    async with running_simulator() as server:
        reader, writer = await asyncio.open_connection(HOST, server.port)
        client = Session(sender="TREBLE", target="SIM")
        writer.write(client.logon(now=NOW))
        await writer.drain()
        stream = read_messages(reader)
        client.receive(await anext(stream))
        assert client.logged_on

        writer.write(
            new_order_single(
                client,
                order_id="ORD1",
                symbol="IBM",
                side="1",
                quantity=1_000_000.0,
                price=98.5,
                now=NOW,
            )
        )
        await writer.drain()
        report = client.receive(await anext(stream))
        assert report.get(35).decode() == EXECUTION_REPORT
        assert report.get(32).decode() == "1000000"
        writer.close()


async def test_several_messages_in_one_read_are_all_drained() -> None:
    """The deadlock this guards against: a loop yielding only the first
    message of a read leaves the rest buffered until the *next* read, which
    never comes if the peer is waiting for a reply. It looks like a slow
    venue rather than a bug."""
    session = Session(sender="SIM", target="TREBLE")
    blob = session.logon(now=NOW) + session.heartbeat(now=NOW) + session.heartbeat(now=NOW)

    reader = asyncio.StreamReader()
    reader.feed_data(blob)
    reader.feed_eof()
    # A read size larger than the whole blob, so all three arrive at once.
    received = [raw async for raw in read_messages(reader, read_size=len(blob) * 2)]
    assert len(received) == 3


async def test_a_message_split_across_reads_is_reassembled() -> None:
    """The other half: one message arriving in pieces must not be parsed as
    a truncated one. A read size of 8 bytes splits a Logon several ways."""
    session = Session(sender="SIM", target="TREBLE")
    raw = session.logon(now=NOW)
    reader = asyncio.StreamReader()
    reader.feed_data(raw)
    reader.feed_eof()
    received = [message async for message in read_messages(reader, read_size=8)]
    assert received == [raw]


async def test_the_stream_ends_cleanly_when_the_peer_closes() -> None:
    """A closed connection is not an error. A transport that raised here
    would turn every normal logout into an exception."""
    reader = asyncio.StreamReader()
    reader.feed_eof()
    assert [message async for message in read_messages(reader)] == []


async def test_the_server_binds_loopback_only() -> None:
    """There is no authentication on this path, so a FIX acceptor reachable
    from a network is one anybody can send orders to."""
    async with running_simulator(Simulator()) as server:
        assert server.port > 0
        _reader, writer = await asyncio.open_connection(HOST, server.port)
        writer.close()
