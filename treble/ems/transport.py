"""A loopback socket for the FIX session (P3_3).

The gate criterion says *connectivity*, and until now there was none: the
session encoded bytes and a test handed them straight to the simulator. That
proved the protocol and nothing about the wire.

**Framing is the whole job, and it is the part a hand-off test cannot
exercise.** TCP is a byte stream with no message boundaries. A read returns
whatever happened to arrive — half a message, three messages, one message
split across two packets — and a receiver that assumes one read is one
message works perfectly on loopback with small messages and fails the first
time a venue sends quickly. FIX is self-delimiting through BodyLength, and
`simplefix.FixParser` buffers on exactly that, so the loop here is: append
whatever arrived, then drain every *complete* message the buffer holds.

Bound to 127.0.0.1 for the same reason the HTTP surface is: there is no
authentication here, and a FIX acceptor reachable from a network is one
anybody can send orders to. §22.1's entitlement model is the prerequisite
for anything else, exactly as it is for federation.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import simplefix

from treble.ems.executions import (
    NotAnExecutionError,
    execution_facts,
    execution_provenance,
    parse_execution,
)
from treble.ems.heartbeat import HeartbeatMonitor, Liveness
from treble.ems.session import TEST_REQ_ID, MsgType
from treble.ems.simulator import Simulator
from treble.ems.store import resume, save
from treble.store.protocols import FactWriter
from treble.vault.worm import ArchivedRecord, Vault

#: Loopback only. See the module docstring.
HOST = "127.0.0.1"

#: Bytes per read. Deliberately small so the framing loop is exercised
#: rather than accidentally satisfied: at 64 bytes a Logon spans several
#: reads, which is the case a larger buffer would hide on loopback.
READ_SIZE = 64

#: How often the session clock is consulted, in seconds. Independent of
#: the heartbeat interval: polling *at* the interval means a heartbeat is
#: on average half an interval late, and a peer that measures strictly
#: will test-request a session that is behaving.
POLL_SECONDS = 0.25


async def read_messages(
    reader: asyncio.StreamReader, *, read_size: int = READ_SIZE
) -> AsyncIterator[bytes]:
    """Yield complete FIX messages from a stream, one at a time.

    The inner `while` matters as much as the outer one. A single read can
    carry several messages, and a loop that yielded only the first would
    leave the rest in the buffer until the *next* read arrived — which, if
    the peer is waiting for a reply, never comes. That is a deadlock that
    looks like a slow venue.
    """
    parser = simplefix.FixParser()
    while True:
        chunk = await reader.read(read_size)
        if not chunk:
            return
        parser.append_buffer(chunk)
        while True:
            message = parser.get_message()
            if message is None:
                break
            yield bytes(message.encode())


class SimulatorServer:
    """The simulator behind a socket."""

    def __init__(
        self,
        simulator: Simulator | None = None,
        *,
        state_dir: Path | None = None,
        vault: Vault | None = None,
        store: FactWriter | None = None,
        heartbeat_seconds: float = 0.0,
    ) -> None:
        self.simulator = simulator or Simulator()
        #: HeartBtInt for connections to this acceptor. Zero — the default
        #: — disables the clock, which is a real FIX setting rather than a
        #: way of not implementing it: a session supervised by other means
        #: needs no filler traffic, and every test that is not about
        #: liveness would otherwise carry a timer.
        self.heartbeat_seconds = heartbeat_seconds
        #: Where the acceptor's sequence counters are persisted. A real
        #: acceptor survives its own restart: counters are per session, not
        #: per connection, so one that began again at 1 would be telling
        #: every client their history never happened.
        self.state_dir = state_dir
        #: Where every message is archived, if anywhere. This is what TVault
        #: is *for*: books-and-records rules require order records and
        #: communications to be retained, and a FIX session is both. Archived
        #: as raw bytes rather than as parsed fields — the record a regulator
        #: asks for is what crossed the wire, not this parser's reading of
        #: it, and the two can differ precisely when it matters.
        self.vault = vault
        #: Where fills are recorded as facts, if anywhere. Requires the
        #: vault: an execution's provenance names the archived message it
        #: was parsed from, so recording without archiving would produce a
        #: fact pointing at bytes nobody kept. Opt-in like archiving, for
        #: the same reason — a transport that wrote to the store by default
        #: would put every test run's fills into it.
        self.store = store
        self._server: asyncio.AbstractServer | None = None
        self.port = 0
        #: Connections dropped for going silent. Counted, because a session
        #: killed for unresponsiveness and one that closed normally are
        #: indistinguishable from the socket alone.
        self.dropped = 0

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        started = datetime.now(UTC)
        monitor = HeartbeatMonitor(
            interval=self.heartbeat_seconds, last_sent=started, last_received=started
        )
        # A separate task, because the read loop below blocks. Draining the
        # socket with a timeout instead would cancel `read_messages`
        # mid-await and destroy the parser's buffer along with whatever
        # partial message was in it — a framing bug that would present as a
        # corrupt peer.
        clock = asyncio.create_task(self._tick(monitor, writer))
        try:
            async for raw in read_messages(reader):
                now = datetime.now(UTC)
                monitor.received(now)
                self._archive(raw, now=now)
                for reply in self.simulator.respond(raw, now=now):
                    # Executions come from the acceptor's *replies*, not
                    # from what the client sent: an order is a request and
                    # only the venue can report a fill.
                    self._record_execution(reply, self._archive(reply, now=now), now=now)
                    writer.write(reply)
                    monitor.sent(now)
                await writer.drain()
                if self.state_dir is not None:
                    # After every exchange, not at shutdown: a crash is the
                    # case this exists for, and a counter flushed at exit is
                    # correct except when it matters.
                    save(self.simulator.session, self.state_dir)
        finally:
            clock.cancel()
            await asyncio.gather(clock, return_exceptions=True)
            writer.close()

    async def _tick(self, monitor: HeartbeatMonitor, writer: asyncio.StreamWriter) -> None:
        """Drive the session clock until the connection goes away.

        `encode` and `write` sit adjacent with no `await` between them.
        Both this task and the read loop send on one session, and an
        `await` in the middle would let the other run between a sequence
        number being consumed and the message carrying it being queued —
        putting two messages on the wire out of order under their own
        numbers, which the peer reads as a gap.
        """
        while True:
            await asyncio.sleep(POLL_SECONDS)
            now = datetime.now(UTC)
            action = monitor.due(now)
            if action is Liveness.IDLE:
                continue
            if action is Liveness.DISCONNECT:
                # The peer stopped answering. Closing is the whole point: a
                # half-open socket accepts writes for ever and reports
                # nothing, so a session that never gave up would look
                # healthy while delivering none of what it was sent.
                self.dropped += 1
                writer.close()
                return
            if action is Liveness.SEND_TEST_REQUEST:
                request_id = f"TR{now.timestamp():.3f}"
                writer.write(
                    self.simulator.session.encode(
                        MsgType.TEST_REQUEST, ((TEST_REQ_ID, request_id),), now=now
                    )
                )
                monitor.test_request_sent(request_id, now)
            else:
                writer.write(self.simulator.session.heartbeat(now=now))
                monitor.sent(now)
            await writer.drain()

    def _archive(self, raw: bytes, *, now: datetime) -> ArchivedRecord | None:
        """Retain one message under the books-and-records schedule.

        The event date is *today* here because a FIX message's event is its
        transmission — unlike a trade confirmation, whose record concerns an
        earlier date. Callers archiving reconstructed history must pass the
        date the record concerns instead, which is why `Vault.archive` takes
        it rather than assuming.

        Returns the record so `_record_execution` can name the archived
        bytes as an execution's provenance. Without that key the fact would
        have to claim "the EMS said so", which is a number with a story
        attached rather than one that can be checked.
        """
        if self.vault is None:
            return None
        return self.vault.archive(raw, kind="fix", event_date=now.date(), now=now)

    def _record_execution(
        self, raw: bytes, archived: ArchivedRecord | None, *, now: datetime
    ) -> None:
        """Write a fill to the fact store, if this message is one.

        **Archive first, then derive** — the same ordering `SourceAdapter.run`
        enforces, and for the same reason: the bytes a record is derived from
        must exist before anything derived from them does. So this refuses to
        write when there is no archived record, rather than falling back to a
        provenance with no payload.

        Most execution reports are not fills — an ack, a cancel and a reject
        all arrive as 35=8 — so `NotAnExecutionError` is the common case and
        is not logged. A malformed message that *is* a fill would be, but
        there is nowhere to log to here and swallowing it would lose a trade
        silently; the recorder is deliberately narrow and anything it cannot
        read stays in the vault to be replayed later.
        """
        if self.store is None or archived is None:
            return
        try:
            execution = parse_execution(raw, received_at=now)
        except NotAnExecutionError:
            return
        provenance = execution_provenance(
            archived.key,
            received_at=now,
            source_uri=f"fix://{self.simulator.session.sender}/{self.simulator.session.target}",
        )
        self.store.write_provenance([provenance])
        self.store.write_facts(list(execution_facts(execution, provenance.id)))

    def _resume(self) -> None:
        """Adopt persisted counters, if any were left by a previous run."""
        if self.state_dir is None:
            return
        session = self.simulator.session
        self.simulator.session = resume(
            self.state_dir, sender=session.sender, target=session.target
        )

    async def start(self, *, port: int = 0) -> int:
        """Bind and return the port actually bound.

        Zero by default, and the default is the one tests use: a fixed
        port makes two runs on one machine collide, and the failure reads
        as a protocol bug rather than as two servers wanting one socket.
        A caller running the acceptor for a real client needs to name a
        port so the client can be pointed at it, which is the only reason
        this is an argument at all.

        The bound port is returned rather than assumed, because with 0 the
        kernel chooses and only it knows.
        """
        self._resume()
        self._server = await asyncio.start_server(self._serve, HOST, port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None


@asynccontextmanager
async def running_simulator(
    simulator: Simulator | None = None,
    *,
    state_dir: Path | None = None,
    vault: Vault | None = None,
    store: FactWriter | None = None,
    heartbeat_seconds: float = 0.0,
) -> AsyncIterator[SimulatorServer]:
    """A simulator listening on loopback, stopped on exit."""
    server = SimulatorServer(
        simulator,
        state_dir=state_dir,
        vault=vault,
        store=store,
        heartbeat_seconds=heartbeat_seconds,
    )
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


__all__ = [
    "HOST",
    "POLL_SECONDS",
    "READ_SIZE",
    "SimulatorServer",
    "read_messages",
    "running_simulator",
]
