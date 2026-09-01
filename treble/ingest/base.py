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
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

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
    #: How often this source expects to have something new, in days. Read by
    #: `ingest.health` to tell a source that has stopped flowing from one
    #: that is merely quiet. None means genuinely irregular — a bulk file
    #: republished when it changes, or an on-demand lookup — and is honest
    #: rather than lazy: inventing a cadence generates false alarms, and a
    #: report that cries wolf is worse than no report.
    expected_cadence_days: float | None = None
    #: How often *we* choose to pull it, in days. ``None`` means "as often
    #: as it publishes" — :attr:`effective_cadence_days`.
    #:
    #: Separate from `expected_cadence_days` because they answer different
    #: questions and only coincide while we fetch everything as fast as it
    #: appears. The first is a fact about the source; this is a decision
    #: about us, usually made on cost. Collapsing them means recording the
    #: decision by overwriting the fact — so a reader later learns that
    #: GLEIF publishes its ISIN mapping weekly, which is not true.
    #:
    #: Set this and the source stays honestly described while the schedule
    #: changes. What it costs is stated at :func:`health.source_health`: a
    #: source pulled less often is also *checked* less often, so a dead
    #: endpoint goes unnoticed for longer.
    fetch_cadence_days: float | None = None

    @property
    def effective_cadence_days(self) -> float | None:
        """The interval anything scheduling or judging this source should use.

        One place for the rule, because the alternative is every caller
        remembering to prefer one field over the other and one of them not.
        """
        return self.fetch_cadence_days or self.expected_cadence_days


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

    def parse_config(self) -> dict[str, Any]:
        """Configuration ``parse`` reads beyond the payload, recorded in the log.

        Empty for most adapters, because `parse` is meant to be a pure
        function of its payload. Three were not: a CIK filter, an ISIN
        filter and a map of acceptance times decided what came out of
        identical bytes, and none of it was written down — so the store
        could not be rebuilt from the log (ADR-0009).

        Anything returned here must be JSON-serialisable and must round-trip
        through :meth:`apply_parse_config`. Overriding one without the other
        gives a log that records configuration replay then ignores, which is
        worse than not recording it: it looks reproducible and is not.
        """
        return {}

    def apply_parse_config(self, config: Mapping[str, Any]) -> None:  # noqa: B027
        """Restore what :meth:`parse_config` recorded, before ``parse`` runs."""

    def run(self) -> Iterator[ParsedBatch]:
        """Fetch, store raw (I5), log, then parse. The ordering is the
        invariant; subclasses cannot reorder it."""
        config = self.parse_config()
        for payload in self.fetch():
            key = self._payloads.put(payload.data)
            self._log.append(
                source=self.meta.source_id,
                payload_hash=key,
                source_uri=payload.source_uri,
                fetched_at=payload.fetched_at,
                parser_version=self.parser_version,
                parse_config=config,
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
