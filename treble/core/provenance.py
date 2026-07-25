"""Provenance — invariant I1 (CLAUDE.md §1, spec §5.4, §7.1 SPTR, §9.4).

Every stored fact references a Provenance record; derived values reference
their inputs' provenance, forming a DAG. Provenance ids are content-addressed
so identical records deduplicate and ids are reproducible under replay (I5).

This module owns the record type and the generic DAG traversal that backs
SPTR. It takes a lookup callable rather than a store handle: core sits at the
bottom of the layering contract and may not import the store.
"""

from __future__ import annotations

import enum
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import NewType

from pydantic import BaseModel, ConfigDict, Field, field_validator

ProvenanceId = NewType("ProvenanceId", str)


class ExtractionMethod(enum.Enum):
    """How a value came to be known."""

    BULK_FILE = "bulk_file"  # published bulk download (companyfacts.zip, GLEIF concatenated)
    API = "api"  # structured API response (FRED, FiscalData, OpenFIGI)
    FEED = "feed"  # streaming/dissemination feed
    XBRL = "xbrl"  # tagged filing data
    DOCUMENT = "document"  # layout-aware / LLM extraction from a document (§9.4)
    HUMAN = "human"  # human-reviewed or community-contributed
    DERIVED = "derived"  # computed from other stored facts (inputs in the DAG)


class Provenance(BaseModel):
    """Where a value came from. Immutable; identity is a content hash."""

    model_config = ConfigDict(frozen=True)

    source_system: str
    source_uri: str
    retrieved_at: datetime
    method: ExtractionMethod
    extractor_version: str
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    locator: str | None = None  # page / XBRL tag / XPath / byte range within the source
    payload_hash: str | None = None  # content address of the raw payload (I5)
    input_ids: tuple[ProvenanceId, ...] = ()  # DAG edges for derived values

    @field_validator("retrieved_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("retrieved_at must be timezone-aware")
        # Canonicalise to UTC: the content-addressed id serialises this field,
        # and the same instant in a different zone must not change the hash.
        return v.astimezone(UTC)

    @property
    def id(self) -> ProvenanceId:
        canonical = self.model_dump_json()
        return ProvenanceId(hashlib.sha256(canonical.encode()).hexdigest())


class ProvenanceTree(BaseModel):
    """A resolved node of the provenance DAG, as SPTR renders it."""

    model_config = ConfigDict(frozen=True)

    record: Provenance
    inputs: tuple[ProvenanceTree, ...] = ()


ProvenanceLookup = Callable[[ProvenanceId], Provenance]


def trace(
    root: ProvenanceId,
    lookup: ProvenanceLookup,
    *,
    max_depth: int = 64,
) -> ProvenanceTree:
    """Resolve the full provenance DAG beneath ``root``.

    This single traversal is the whole of SPTR's logic (I1): screens render
    the tree, they do not implement per-field tracing.
    """
    if max_depth < 0:
        raise ValueError("provenance DAG deeper than max_depth — cycle or corrupt data")
    record = lookup(root)
    inputs = tuple(trace(i, lookup, max_depth=max_depth - 1) for i in record.input_ids)
    return ProvenanceTree(record=record, inputs=inputs)
