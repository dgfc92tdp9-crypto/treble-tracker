"""The seam between the plant and a message broker (spec §8.1, §13.2).

The spec names two brokers for two jobs: Redpanda/Kafka as the durable log
that makes replay deterministic, and NATS JetStream for low-latency fanout
to clients. This is the interface both sides of that are written against, so
the plant does not know which one it is talking to — and so the in-process
path is not a different program from the distributed one.

**One protocol, more than one implementation, one test suite.** The same
reason `render/` has a conformance suite: a transport that is only exercised
in-process is a transport whose wire format has never been tested, and the
failure shows up as a tick that arrives subtly wrong rather than as an
exception. `tests/plant/test_transport_conformance.py` runs every
implementation through identical assertions, against a real broker where
there is one.

**Subject encoding, and what it is and is not protecting against.** NATS
splits subjects on `.` and treats `*` and `>` as wildcards, and a TUID is
arbitrary text — `swaption:EUR:...`, `cusip:037833100`, and whatever a venue
puts in an instrument id. Measured against a live server: a raw dotted id
published as `ticks.has.dot` is accepted, retained, and never delivered to a
subscriber on `ticks.*`, because it is three tokens rather than two. Silent
loss, no error.

That defect is **not currently reachable here**, and saying so matters more
than the escaping does. `subject_pattern` uses `ticks.>` for the
all-instruments case, which matches any depth, and an exact subject for the
per-instrument case, which matches itself. Under both, a dotted id is
delivered — verified end to end against a real broker, and the end-to-end
test was left in place precisely because it passes with the escaping removed
and would otherwise read as a regression guard it is not.

So the escaping is defensive: it keeps a single-level wildcard from becoming
a silent-loss bug the day someone introduces one. `.` is escaped explicitly
because Python's `quote()` keeps it unescaped whatever `safe` says, which is
how it would have got in. The property tests in `TestSubjectEncoding` are
the ones that fail when the escaping is removed; they were mutation-checked,
and the end-to-end one was checked too — which is how it was found not to
be a guard at all.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable
from urllib.parse import quote, unquote

from treble.core.identifiers import TUID
from treble.plant.conflation import Tick

#: Root of the subject hierarchy. Everything the plant publishes lives under
#: it, so a broker can carry other traffic without a wildcard subscription
#: picking it up.
SUBJECT_ROOT = "ticks"


def encode_token(text: str) -> str:
    """Percent-encode one NATS subject token.

    `quote(safe="")` is not enough on its own: Python keeps `.` unescaped
    whatever `safe` says, and a dot inside a token silently becomes a subject
    separator. That is not a formatting nit — a tick published under a
    dotted id is accepted by the broker, retained in the stream, and never
    delivered to a subscriber matching a single level.
    """
    return quote(text, safe="").replace(".", "%2E")


def decode_token(token: str) -> str:
    """Inverse of :func:`encode_token`."""
    return unquote(token)


def subject_for(subject: TUID) -> str:
    """The NATS subject one instrument's ticks are published on."""
    return f"{SUBJECT_ROOT}.{encode_token(str(subject))}"


def subject_pattern(subject: TUID | None) -> str:
    """The subscription pattern for one instrument, or for all of them.

    Single-level `*` for one instrument rather than `>`: a trailing `>`
    matches deeper subjects too, so any future `ticks.<id>.<something>`
    would start arriving on a subscription that asked for ticks.
    """
    return f"{SUBJECT_ROOT}.>" if subject is None else subject_for(subject)


def encode_tick(tick: Tick) -> bytes:
    """Serialise a tick for the wire.

    JSON rather than a binary codec, because the durable log is the thing
    replay reads and a log nobody can inspect without the exact library
    version that wrote it is a worse foundation for reproducibility than a
    few bytes of overhead. The conformance suite pins that a round-trip is
    exact rather than close — a price that changes in the last decimal
    somewhere between two processes is the worst defect this layer can have,
    and the one least likely to be noticed.
    """
    return tick.model_dump_json().encode()


def decode_tick(payload: bytes) -> Tick:
    """Inverse of :func:`encode_tick`."""
    return Tick.model_validate_json(payload)


@runtime_checkable
class TickTransport(Protocol):
    """What the plant needs of a broker, and nothing more.

    Deliberately small. A transport that exposed the broker's own client
    would put Kafka's or NATS's vocabulary into the plant, and the seam
    exists so that neither leaks.
    """

    async def publish(self, tick: Tick) -> None:
        """Send one tick. Must not block on any subscriber."""
        ...

    async def subscribe(self, subject: TUID | None = None) -> AsyncIterator[Tick]:
        """Live ticks for one instrument, or for all of them.

        Awaited, and it must be: the subscription has to exist before it
        returns. An earlier version made this a bare async generator, so
        nothing registered with the broker until the first read — and a
        publish between the call and that first read was accepted, retained,
        and never delivered. Both implementations hung on the first
        conformance test, which is the cheap version of that bug.
        """
        ...

    async def close(self) -> None:
        """Release the connection. Safe to call twice."""
        ...


class InProcessTransport:
    """The same protocol with no broker, for a single-process workstation.

    Not a test double. `treble init` builds a local-only install with no
    broker to connect to, and this is the path that install runs on — which
    is also why it is held to the same conformance suite as the NATS
    implementation rather than being assumed correct because it is simple.

    It encodes and decodes on the way through despite there being no wire.
    That looks wasteful and is deliberate: if the local path skipped the
    codec, a tick that fails to serialise would work locally and fail only
    once a broker was introduced, which is the failure this seam exists to
    prevent.
    """

    def __init__(self) -> None:
        self._queues: list[tuple[TUID | None, asyncio.Queue[bytes]]] = []
        self._closed = False

    async def publish(self, tick: Tick) -> None:
        payload = encode_tick(tick)
        for wanted, queue in self._queues:
            if wanted is None or wanted == tick.subject:
                queue.put_nowait(payload)

    async def subscribe(self, subject: TUID | None = None) -> AsyncIterator[Tick]:
        # Registered here, before returning, so that a publish issued after
        # this await is guaranteed to be seen by the iterator.
        queue: asyncio.Queue[bytes] = asyncio.Queue()
        entry: tuple[TUID | None, asyncio.Queue[bytes]] = (subject, queue)
        self._queues.append(entry)
        return self._drain(entry)

    async def _drain(self, entry: tuple[TUID | None, asyncio.Queue[bytes]]) -> AsyncIterator[Tick]:
        try:
            while True:
                yield decode_tick(await entry[1].get())
        finally:
            if entry in self._queues:
                self._queues.remove(entry)

    async def close(self) -> None:
        self._closed = True
        self._queues.clear()


__all__ = [
    "SUBJECT_ROOT",
    "InProcessTransport",
    "TickTransport",
    "decode_tick",
    "decode_token",
    "encode_tick",
    "encode_token",
    "subject_for",
    "subject_pattern",
]
