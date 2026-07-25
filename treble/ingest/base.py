"""SourceAdapter — the contract every ingest adapter implements (CLAUDE.md §6).

The template method :meth:`SourceAdapter.run` enforces I5 mechanically:
raw bytes are written to the content-addressed payload store and the
append-only ingest log **before** ``parse`` is invoked. Adapters implement
``fetch`` and ``parse`` only; they cannot skip storage, and ``parse`` must be
a pure function of (payload, parser_version) — no clocks, no network, no
global state — or the replay test diverges.

Every adapter declares its licence terms and redistribution conditions in
metadata; the CUSIP redistribution guard (spec §9.3) keys off these flags.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from treble.core.facts import Fact
from treble.core.provenance import Provenance
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore


class TokenBucket:
    """Simple thread-safe token bucket for per-source rate limits
    (EDGAR: 10 req/s; OpenFIGI: 25/min unauthenticated — CLAUDE.md §6)."""

    def __init__(self, rate_per_second: float, burst: int = 1) -> None:
        self._rate = rate_per_second
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            time.sleep(wait)


class SourceMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str  # e.g. "edgar", "fred"
    description: str
    licence: str  # the source's stated terms, quoted or referenced
    redistribution_restricted: bool = False  # drives the bulk-export guard
    rate_limit_per_second: float | None = None


class RawPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    data: bytes
    source_uri: str
    fetched_at: datetime


class ParsedBatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    provenance: tuple[Provenance, ...]
    facts: tuple[Fact, ...]


class SourceAdapter(ABC):
    """Base adapter. Subclasses set ``meta`` and ``parser_version`` and
    implement ``fetch`` and ``parse``."""

    meta: SourceMeta
    parser_version: str

    def __init__(self, payloads: PayloadStore, log: IngestLog) -> None:
        self._payloads = payloads
        self._log = log
        rate = self.meta.rate_limit_per_second
        self._bucket = TokenBucket(rate) if rate else None

    def _throttle(self) -> None:
        if self._bucket is not None:
            self._bucket.acquire()

    @abstractmethod
    def fetch(self) -> Iterator[RawPayload]:
        """Yield raw payloads from the source. Network happens here and only
        here. Recorded-fixture tests bypass this entirely."""

    @abstractmethod
    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        """Pure function of (payload bytes, parser_version) -> facts with
        provenance. The knowledge date (I2) comes from the payload content
        (e.g. EDGAR ``accepted``) or, failing that, ``payload.fetched_at`` —
        never from the wall clock."""

    def run(self) -> Iterator[ParsedBatch]:
        """Fetch, store raw (I5), log, then parse. The ordering is the
        invariant; subclasses cannot reorder it."""
        for payload in self.fetch():
            key = self._payloads.put(payload.data)
            self._log.append(
                source=self.meta.source_id,
                payload_hash=key,
                source_uri=payload.source_uri,
                fetched_at=payload.fetched_at,
                parser_version=self.parser_version,
            )
            yield self.parse(payload, key)

    def replay(self, *, up_to_seq: int | None = None) -> Iterator[ParsedBatch]:
        """Re-parse from the stored log without touching the network (I5)."""
        for entry in self._log.read(up_to_seq=up_to_seq):
            if entry.source != self.meta.source_id:
                continue
            data = self._payloads.get(entry.payload_hash)
            # The original URI is reconstructed from the log so replayed
            # provenance is byte-identical to the original run (I5).
            payload = RawPayload(
                data=data,
                source_uri=entry.source_uri,
                fetched_at=entry.fetched_at,
            )
            yield self.parse(payload, entry.payload_hash)


def utcnow() -> datetime:
    """The single sanctioned wall-clock read for fetch timestamps."""
    return datetime.now(UTC)
