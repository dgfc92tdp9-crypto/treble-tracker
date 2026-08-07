"""The `docs` service: the source documents behind a subject (spec §8.3).

`SPTR` renders the provenance DAG — which model, which inputs, which
extraction. This answers the question one step further down: *show me the
document itself*. Every fact carries a provenance record, every provenance
record names a payload hash, and the payload store holds those bytes
unaltered. So the documents behind a subject are already there; nothing new
is stored to serve them.

**Content-addressed, so a document cannot have changed.** The payload hash
*is* the SHA-256 of the bytes, and `PayloadStore.get` returns exactly what
was fetched. A document served here is the one the facts were parsed from,
not a re-fetch that may since have been revised — which is the difference
between provenance and a hyperlink. EDGAR restates, vendors correct, and a
link resolves to today's version while the fact was parsed from an earlier
one.

**Bytes are not returned by default.** A single EDGAR bulk payload runs to
hundreds of megabytes, and a screen listing a subject's documents wants the
list rather than the contents. :func:`documents_for` returns descriptors and
:func:`document_bytes` fetches one deliberately.

**Redistribution restrictions travel with the document, not just the fact.**
A document from a `redistribution_restricted` source is the most concentrated
form of that source's data — the whole payload rather than the fields parsed
out of it — so the descriptor carries the flag and the bulk-export guard has
something to key on. Serving the parsed facts under a restriction while
serving the document freely would make the guard decorative.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from treble.core.identifiers import TUID
from treble.core.provenance import ProvenanceId
from treble.ingest.registry import restricted_source_ids
from treble.store.duck import DuckStore
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore


#: Sources whose payloads may not be redistributed, read from the adapters'
#: own `SourceMeta.redistribution_restricted` via the registry.
#:
#: This was a hardcoded set, justified in a comment claiming `tapi` must not
#: import `ingest`. That claim was false — `tapi/export.py` has imported
#: `restricted_source_ids` since the export guard was built, and `ingest` is
#: not in the layered contract at all. So the duplication bought nothing and
#: cost the thing duplication always costs: a second list to forget. It
#: already disagreed with the registry, listing three sources where the
#: adapters declare more.
#:
#: One source of truth, and it is the adapter, because that is where the
#: licence was read.
def restricted_sources() -> frozenset[str]:
    """Source ids whose payloads may not leave this install."""
    return restricted_source_ids()


class DocumentUnavailableError(RuntimeError):
    """No such document, with what was looked for."""


@dataclass(frozen=True)
class DocumentRef:
    """One source document behind a subject's facts."""

    payload_hash: str
    source_system: str
    source_uri: str
    retrieved_at: datetime
    #: How many of this subject's facts came from this document. A payload
    #: that produced one field and one that produced four hundred are
    #: different things to a reader deciding what to open.
    fact_count: int
    #: Whether the source forbids redistributing the payload.
    redistribution_restricted: bool


def documents_for(store: DuckStore, subject: TUID, *, as_of: datetime) -> list[DocumentRef]:
    """Every source document behind a subject's facts, newest first.

    Point-in-time through the store's own reads (I2): asking what documents
    a subject had on Tuesday must give Tuesday's answer, not today's, or the
    drill-down disagrees with the screen it was opened from.
    """
    facts = store.subject_facts(subject, as_of=as_of)
    if not facts:
        raise DocumentUnavailableError(
            f"{subject} has no facts as of {as_of.isoformat()}, so it has no documents. "
            "An unknown subject and a subject with no sources are different, and this "
            "is the first"
        )
    counts: dict[ProvenanceId, int] = {}
    for fact in facts:
        counts[fact.provenance_id] = counts.get(fact.provenance_id, 0) + 1

    restricted = restricted_sources()
    refs: list[DocumentRef] = []
    seen: set[str] = set()
    for provenance_id, count in counts.items():
        record = store.provenance(provenance_id)
        # `payload_hash` is optional on Provenance: a derived value has a
        # provenance record and no document behind it. Those are skipped
        # rather than listed with an empty hash, because a document nobody
        # can open is worse on a screen than one that is not offered.
        if record.payload_hash is None or record.payload_hash in seen:
            continue
        seen.add(record.payload_hash)
        refs.append(
            DocumentRef(
                payload_hash=record.payload_hash,
                source_system=record.source_system,
                source_uri=record.source_uri,
                retrieved_at=record.retrieved_at,
                fact_count=count,
                redistribution_restricted=record.source_system in restricted,
            )
        )
    return sorted(refs, key=lambda r: r.retrieved_at, reverse=True)


def document_bytes(payloads: PayloadStore, payload_hash: str) -> bytes:
    """The document itself, exactly as fetched.

    Content-addressed: what comes back hashes to the key it was asked for,
    so this cannot serve a revised version of the document the facts were
    parsed from.
    """
    key = PayloadHash(payload_hash)
    if not payloads.exists(key):
        raise DocumentUnavailableError(
            f"payload {payload_hash[:12]}… is referenced by provenance but absent from "
            "the payload store. That is a broken I5 replay chain rather than a missing "
            "file: the facts derived from it can no longer be reproduced"
        )
    return payloads.get(key)


def ingest_history(log: IngestLog, *, source: str | None = None) -> list[dict[str, object]]:
    """What was ingested, when, and by which parser version.

    The log rather than the facts, because the two answer different
    questions. "This adapter ingested nothing" and "this adapter ingested a
    payload that produced no facts" render the same on a screen built from
    facts alone, and only the second is a parser problem.
    """
    return [
        {
            "seq": entry.seq,
            "source": entry.source,
            "payload_hash": str(entry.payload_hash),
            "source_uri": entry.source_uri,
            "fetched_at": entry.fetched_at,
            "parser_version": entry.parser_version,
        }
        for entry in log.read()
        if source is None or entry.source == source
    ]


__all__ = [
    "DocumentRef",
    "DocumentUnavailableError",
    "document_bytes",
    "documents_for",
    "ingest_history",
    "restricted_sources",
]
