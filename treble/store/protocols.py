"""Store protocols (CLAUDE.md §3).

Deliberately narrow: there are **no update or delete members** on any of
these protocols. Append-only is a property of the interface (I2, I5), not a
discipline. Pointing the system at different storage means implementing
these; nothing above the store may assume DuckDB.

Every read takes ``as_of`` as a required keyword parameter (I2). The
"default is now" convenience lives at the TAPI boundary, not here — a caller
of the store cannot forget the knowledge date.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import Provenance, ProvenanceId
from treble.store.ingest_log import IngestLogEntry
from treble.store.payloads import PayloadHash


class PayloadStoreP(Protocol):
    def put(self, data: bytes) -> PayloadHash: ...
    def get(self, key: PayloadHash) -> bytes: ...
    def exists(self, key: PayloadHash) -> bool: ...


class IngestLogP(Protocol):
    def append(
        self,
        *,
        source: str,
        payload_hash: PayloadHash,
        source_uri: str,
        fetched_at: datetime,
        parser_version: str,
    ) -> IngestLogEntry: ...

    def read(self, *, up_to_seq: int | None = None) -> list[IngestLogEntry]: ...


class FactWriter(Protocol):
    """The write half of a store, and nothing else.

    Narrower than :class:`Store` on purpose, for callers that must not read
    from what they are writing to. A replay rebuilding a store typed as
    `Store` could start reading the thing it is reconstructing; the EMS
    recording a fill has no business querying the book it is appending to.
    Both take this instead, so the restriction is in the type rather than in
    a comment asking future edits to be careful.
    """

    def write_provenance(self, records: list[Provenance]) -> None: ...
    def write_facts(self, facts: list[Fact]) -> None: ...


class Store(Protocol):
    """Current-state and point-in-time reads over bitemporal facts."""

    def write_provenance(self, records: list[Provenance]) -> None: ...
    def write_facts(self, facts: list[Fact]) -> None: ...

    def read(
        self,
        subject: TUID,
        field: str,
        *,
        as_of: datetime,
    ) -> list[Fact]: ...

    def provenance(self, provenance_id: ProvenanceId) -> Provenance: ...


class HistoryStore(Protocol):
    """Effective-time series, point-in-time correct in knowledge time."""

    def history(
        self,
        subject: TUID,
        field: str,
        *,
        as_of: datetime,
        effective_from: date | None = None,
        effective_to: date | None = None,
    ) -> list[Fact]: ...
