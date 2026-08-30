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


async def test_the_acceptor_resumes_its_counters_across_a_restart(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A real acceptor survives its own restart. Counters are per session,
    not per connection, so one that began again at 1 would be telling every
    client their history never happened.

    Written because `treble.ems.store` had no caller in the package — the
    persistence existed and nothing used it, which is the shape this
    repository keeps finding. Wiring it here is the fix; this is the proof.
    """
    from treble.ems.store import resume

    async with running_simulator(state_dir=tmp_path) as server:
        reader, writer = await asyncio.open_connection(HOST, server.port)
        client = Session(sender="TREBLE", target="SIM")
        writer.write(client.logon(now=NOW))
        await writer.drain()
        client.receive(await anext(read_messages(reader)))
        writer.close()
        after_first = server.simulator.session.outbound_seq

    assert after_first > 1
    saved = resume(tmp_path, sender="SIM", target="TREBLE")
    assert saved.outbound_seq == after_first

    # A second server on the same directory picks the counters back up.
    async with running_simulator(state_dir=tmp_path) as second:
        assert second.simulator.session.outbound_seq == after_first


async def test_every_message_is_archived_when_a_vault_is_given(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Books-and-records rules require order records and communications to
    be retained, and a FIX session is both. Raw bytes are archived rather
    than parsed fields: the record a regulator asks for is what crossed the
    wire, not this parser's reading of it, and the two can differ precisely
    when it matters.

    Written because `treble.vault.worm` had no caller in the package — the
    retention machinery existed and nothing archived into it.
    """
    from treble.vault.worm import Vault

    vault = Vault(tmp_path / "vault")
    async with running_simulator(vault=vault) as server:
        reader, writer = await asyncio.open_connection(HOST, server.port)
        client = Session(sender="TREBLE", target="SIM")
        writer.write(client.logon(now=NOW))
        await writer.drain()
        client.receive(await anext(read_messages(reader)))
        writer.close()

    # The client's Logon and the acceptor's reply: both directions retained.
    assert len(vault) == 2
    assert all(record.kind == "fix" for record in vault.due_for_destruction(today=NOW.date()) or [])


async def test_nothing_is_archived_without_a_vault(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Archiving is opt-in. A transport that retained by default would put
    every test run under a seven-year schedule."""
    from treble.vault.worm import Vault

    vault = Vault(tmp_path / "unused")
    async with running_simulator() as server:
        reader, writer = await asyncio.open_connection(HOST, server.port)
        client = Session(sender="TREBLE", target="SIM")
        writer.write(client.logon(now=NOW))
        await writer.drain()
        client.receive(await anext(read_messages(reader)))
        writer.close()
    assert len(vault) == 0


async def test_a_fill_is_recorded_as_facts_with_the_archived_message_as_provenance(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """The half of P3_5 the ledger called buildable.

    TCA needs executions to analyse and this install had none: `ems/store.py`
    persists sequence numbers and `tapi/positions.py` reads *fund* holdings
    out of N-PORT. Neither is a record of what this workstation traded.

    The provenance assertion is the point. An execution fact whose
    provenance says only "the EMS said so" is a number with a story
    attached; this one names the archived FIX message it was parsed from, so
    the bytes behind a fill can be fetched back and re-read.
    """
    from treble.ems.executions import EXECUTION_FIELDS, EXECUTION_PREFIX
    from treble.ems.simulator import new_order_single
    from treble.store.duck import DuckStore
    from treble.vault.worm import Vault

    vault = Vault(tmp_path / "vault")
    store = DuckStore(tmp_path / "book.db")

    async with running_simulator(vault=vault, store=store) as server:
        reader, writer = await asyncio.open_connection(HOST, server.port)
        client = Session(sender="TREBLE", target="SIM")
        writer.write(client.logon(now=NOW))
        await writer.drain()
        client.receive(await anext(read_messages(reader)))
        writer.write(
            new_order_single(
                client,
                now=NOW,
                order_id="ORD1",
                symbol="IBM",
                side="1",
                quantity=100.0,
                price=250.0,
            )
        )
        await writer.drain()
        client.receive(await anext(read_messages(reader)))
        writer.close()

    # `as_of` is *now*, not the test's NOW: the transport stamps a fill
    # with the wall clock it actually happened at, so a read positioned at
    # a 2026-08-11 constant would look before the fact existed and find
    # nothing — which would pass a test asserting absence and fail this one
    # for the wrong reason.
    seen = datetime.now(UTC)
    subjects = store.subjects_with_prefix(EXECUTION_PREFIX, as_of=seen)
    assert len(subjects) == 1, "one fill, one execution subject"

    facts = [
        fact for field in EXECUTION_FIELDS for fact in store.read(subjects[0], field, as_of=seen)
    ]
    assert {f.field for f in facts} == set(EXECUTION_FIELDS)

    by_field = {f.field: f.value for f in facts}
    assert by_field["ems:exec:symbol"] == "IBM"
    assert by_field["ems:exec:side"] == "buy"
    assert by_field["ems:exec:lastQty"] == 100.0
    assert by_field["ems:exec:lastPx"] == 250.0

    # I1: the provenance names bytes that are actually in the vault.
    record = store.provenance(facts[0].provenance_id)
    assert record.source_system == "ems"
    assert record.payload_hash, "an execution's provenance must name its message"
    assert vault.read(record.payload_hash), "and those bytes must be retrievable"


async def test_nothing_is_recorded_without_a_store(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Opt-in like archiving: a transport writing to the book by default
    would put every test run's fills into it."""
    from treble.ems.executions import EXECUTION_PREFIX
    from treble.ems.simulator import new_order_single
    from treble.store.duck import DuckStore
    from treble.vault.worm import Vault

    store = DuckStore(tmp_path / "book.db")
    async with running_simulator(vault=Vault(tmp_path / "vault")) as server:
        reader, writer = await asyncio.open_connection(HOST, server.port)
        client = Session(sender="TREBLE", target="SIM")
        writer.write(client.logon(now=NOW))
        await writer.drain()
        client.receive(await anext(read_messages(reader)))
        writer.write(
            new_order_single(
                client,
                now=NOW,
                order_id="ORD1",
                symbol="IBM",
                side="1",
                quantity=100.0,
                price=250.0,
            )
        )
        await writer.drain()
        client.receive(await anext(read_messages(reader)))
        writer.close()

    assert store.subjects_with_prefix(EXECUTION_PREFIX, as_of=datetime.now(UTC)) == []


async def test_a_store_without_a_vault_records_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Recording requires archiving, and the ordering is the invariant.

    An execution's provenance names the archived message it was parsed
    from, so recording without archiving would write a fact pointing at
    bytes nobody kept — the same failure as an ingest adapter parsing
    before storing its payload, which `SourceAdapter.run` makes impossible
    by construction.
    """
    from treble.ems.executions import EXECUTION_PREFIX
    from treble.ems.simulator import new_order_single
    from treble.store.duck import DuckStore

    store = DuckStore(tmp_path / "book.db")
    async with running_simulator(store=store) as server:
        reader, writer = await asyncio.open_connection(HOST, server.port)
        client = Session(sender="TREBLE", target="SIM")
        writer.write(client.logon(now=NOW))
        await writer.drain()
        client.receive(await anext(read_messages(reader)))
        writer.write(
            new_order_single(
                client,
                now=NOW,
                order_id="ORD1",
                symbol="IBM",
                side="1",
                quantity=100.0,
                price=250.0,
            )
        )
        await writer.drain()
        client.receive(await anext(read_messages(reader)))
        writer.close()

    assert store.subjects_with_prefix(EXECUTION_PREFIX, as_of=datetime.now(UTC)) == []
