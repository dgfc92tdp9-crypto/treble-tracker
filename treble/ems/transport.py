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

from treble.ems.simulator import Simulator
from treble.ems.store import resume, save
from treble.vault.worm import Vault

#: Loopback only. See the module docstring.
HOST = "127.0.0.1"

#: Bytes per read. Deliberately small so the framing loop is exercised
#: rather than accidentally satisfied: at 64 bytes a Logon spans several
#: reads, which is the case a larger buffer would hide on loopback.
READ_SIZE = 64


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
    ) -> None:
        self.simulator = simulator or Simulator()
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
        self._server: asyncio.AbstractServer | None = None
        self.port = 0

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async for raw in read_messages(reader):
                now = datetime.now(UTC)
                self._archive(raw, now=now)
                for reply in self.simulator.respond(raw, now=now):
                    self._archive(reply, now=now)
                    writer.write(reply)
                await writer.drain()
                if self.state_dir is not None:
                    # After every exchange, not at shutdown: a crash is the
                    # case this exists for, and a counter flushed at exit is
                    # correct except when it matters.
                    save(self.simulator.session, self.state_dir)
        finally:
            writer.close()

    def _archive(self, raw: bytes, *, now: datetime) -> None:
        """Retain one message under the books-and-records schedule.

        The event date is *today* here because a FIX message's event is its
        transmission — unlike a trade confirmation, whose record concerns an
        earlier date. Callers archiving reconstructed history must pass the
        date the record concerns instead, which is why `Vault.archive` takes
        it rather than assuming.
        """
        if self.vault is None:
            return
        self.vault.archive(raw, kind="fix", event_date=now.date(), now=now)

    def _resume(self) -> None:
        """Adopt persisted counters, if any were left by a previous run."""
        if self.state_dir is None:
            return
        session = self.simulator.session
        self.simulator.session = resume(
            self.state_dir, sender=session.sender, target=session.target
        )

    async def start(self) -> int:
        """Bind an ephemeral port and return it.

        Port 0 rather than a fixed one: a fixed port makes two test runs on
        one machine collide, and the failure reads as a protocol bug.
        """
        self._resume()
        self._server = await asyncio.start_server(self._serve, HOST, 0)
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
) -> AsyncIterator[SimulatorServer]:
    """A simulator listening on loopback, stopped on exit."""
    server = SimulatorServer(simulator, state_dir=state_dir, vault=vault)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


__all__ = [
    "HOST",
    "READ_SIZE",
    "SimulatorServer",
    "read_messages",
    "running_simulator",
]
