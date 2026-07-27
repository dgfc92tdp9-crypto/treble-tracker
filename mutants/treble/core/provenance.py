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


from mutmut.mutation.trampoline import wrap_in_trampoline as _mutmut_mutated, MutantDict


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
mutants_x_trace__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_trace__mutmut)
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


def x_trace__mutmut_orig(
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


def x_trace__mutmut_1(
    root: ProvenanceId,
    lookup: ProvenanceLookup,
    *,
    max_depth: int = 65,
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


def x_trace__mutmut_2(
    root: ProvenanceId,
    lookup: ProvenanceLookup,
    *,
    max_depth: int = 64,
) -> ProvenanceTree:
    """Resolve the full provenance DAG beneath ``root``.

    This single traversal is the whole of SPTR's logic (I1): screens render
    the tree, they do not implement per-field tracing.
    """
    if max_depth <= 0:
        raise ValueError("provenance DAG deeper than max_depth — cycle or corrupt data")
    record = lookup(root)
    inputs = tuple(trace(i, lookup, max_depth=max_depth - 1) for i in record.input_ids)
    return ProvenanceTree(record=record, inputs=inputs)


def x_trace__mutmut_3(
    root: ProvenanceId,
    lookup: ProvenanceLookup,
    *,
    max_depth: int = 64,
) -> ProvenanceTree:
    """Resolve the full provenance DAG beneath ``root``.

    This single traversal is the whole of SPTR's logic (I1): screens render
    the tree, they do not implement per-field tracing.
    """
    if max_depth < 1:
        raise ValueError("provenance DAG deeper than max_depth — cycle or corrupt data")
    record = lookup(root)
    inputs = tuple(trace(i, lookup, max_depth=max_depth - 1) for i in record.input_ids)
    return ProvenanceTree(record=record, inputs=inputs)


def x_trace__mutmut_4(
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
        raise ValueError(None)
    record = lookup(root)
    inputs = tuple(trace(i, lookup, max_depth=max_depth - 1) for i in record.input_ids)
    return ProvenanceTree(record=record, inputs=inputs)


def x_trace__mutmut_5(
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
        raise ValueError("XXprovenance DAG deeper than max_depth — cycle or corrupt dataXX")
    record = lookup(root)
    inputs = tuple(trace(i, lookup, max_depth=max_depth - 1) for i in record.input_ids)
    return ProvenanceTree(record=record, inputs=inputs)


def x_trace__mutmut_6(
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
        raise ValueError("provenance dag deeper than max_depth — cycle or corrupt data")
    record = lookup(root)
    inputs = tuple(trace(i, lookup, max_depth=max_depth - 1) for i in record.input_ids)
    return ProvenanceTree(record=record, inputs=inputs)


def x_trace__mutmut_7(
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
        raise ValueError("PROVENANCE DAG DEEPER THAN MAX_DEPTH — CYCLE OR CORRUPT DATA")
    record = lookup(root)
    inputs = tuple(trace(i, lookup, max_depth=max_depth - 1) for i in record.input_ids)
    return ProvenanceTree(record=record, inputs=inputs)


def x_trace__mutmut_8(
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
    record = None
    inputs = tuple(trace(i, lookup, max_depth=max_depth - 1) for i in record.input_ids)
    return ProvenanceTree(record=record, inputs=inputs)


def x_trace__mutmut_9(
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
    record = lookup(None)
    inputs = tuple(trace(i, lookup, max_depth=max_depth - 1) for i in record.input_ids)
    return ProvenanceTree(record=record, inputs=inputs)


def x_trace__mutmut_10(
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
    inputs = None
    return ProvenanceTree(record=record, inputs=inputs)


def x_trace__mutmut_11(
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
    inputs = tuple(None)
    return ProvenanceTree(record=record, inputs=inputs)


def x_trace__mutmut_12(
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
    inputs = tuple(trace(None, lookup, max_depth=max_depth - 1) for i in record.input_ids)
    return ProvenanceTree(record=record, inputs=inputs)


def x_trace__mutmut_13(
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
    inputs = tuple(trace(i, None, max_depth=max_depth - 1) for i in record.input_ids)
    return ProvenanceTree(record=record, inputs=inputs)


def x_trace__mutmut_14(
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
    inputs = tuple(trace(i, lookup, max_depth=None) for i in record.input_ids)
    return ProvenanceTree(record=record, inputs=inputs)


def x_trace__mutmut_15(
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
    inputs = tuple(trace(lookup, max_depth=max_depth - 1) for i in record.input_ids)
    return ProvenanceTree(record=record, inputs=inputs)


def x_trace__mutmut_16(
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
    inputs = tuple(trace(i, max_depth=max_depth - 1) for i in record.input_ids)
    return ProvenanceTree(record=record, inputs=inputs)


def x_trace__mutmut_17(
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
    inputs = tuple(trace(i, lookup, ) for i in record.input_ids)
    return ProvenanceTree(record=record, inputs=inputs)


def x_trace__mutmut_18(
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
    inputs = tuple(trace(i, lookup, max_depth=max_depth + 1) for i in record.input_ids)
    return ProvenanceTree(record=record, inputs=inputs)


def x_trace__mutmut_19(
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
    inputs = tuple(trace(i, lookup, max_depth=max_depth - 2) for i in record.input_ids)
    return ProvenanceTree(record=record, inputs=inputs)


def x_trace__mutmut_20(
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
    return ProvenanceTree(record=None, inputs=inputs)


def x_trace__mutmut_21(
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
    return ProvenanceTree(record=record, inputs=None)


def x_trace__mutmut_22(
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
    return ProvenanceTree(inputs=inputs)


def x_trace__mutmut_23(
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
    return ProvenanceTree(record=record, )

mutants_x_trace__mutmut['_mutmut_orig'] = x_trace__mutmut_orig # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_1'] = x_trace__mutmut_1 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_2'] = x_trace__mutmut_2 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_3'] = x_trace__mutmut_3 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_4'] = x_trace__mutmut_4 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_5'] = x_trace__mutmut_5 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_6'] = x_trace__mutmut_6 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_7'] = x_trace__mutmut_7 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_8'] = x_trace__mutmut_8 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_9'] = x_trace__mutmut_9 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_10'] = x_trace__mutmut_10 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_11'] = x_trace__mutmut_11 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_12'] = x_trace__mutmut_12 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_13'] = x_trace__mutmut_13 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_14'] = x_trace__mutmut_14 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_15'] = x_trace__mutmut_15 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_16'] = x_trace__mutmut_16 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_17'] = x_trace__mutmut_17 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_18'] = x_trace__mutmut_18 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_19'] = x_trace__mutmut_19 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_20'] = x_trace__mutmut_20 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_21'] = x_trace__mutmut_21 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_22'] = x_trace__mutmut_22 # type: ignore # mutmut generated
mutants_x_trace__mutmut['x_trace__mutmut_23'] = x_trace__mutmut_23 # type: ignore # mutmut generated
