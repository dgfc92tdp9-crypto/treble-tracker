"""Entity graph — parent/subsidiary and fund structure over LEIs (spec §9.5; WP7).

Built from GLEIF Level 2 Relationship Record facts
(``treble.ingest.gleif.GleifRelationshipAdapter``), the same way
``treble.core.master`` builds instrument identity from OpenFIGI/N-PORT
facts: pure functions over stored facts, so the graph is reproducible by
replay (I5) and every edge answers "why" through SPTR via its
``provenance_id`` (I1).

GLEIF asserts both direct and ultimate consolidation directly per entity
(``IS_DIRECTLY_CONSOLIDATED_BY`` / ``IS_ULTIMATELY_CONSOLIDATED_BY``) — this
module does not walk direct-parent edges to infer an ultimate parent, since
GLEIF's own ultimate-parent assertion may reflect information (e.g. a
distant holding company with no LEI-visible intermediate chain) that a walk
would miss or get wrong. Each lookup is a single-hop resolution against the
relevant relationship type, exactly as the source published it.

Where GLEIF has asserted more than one parent of the same type for an
entity, that is reported, never silently resolved by preferring one
(working agreement: no fabrication — mirrors ``treble.core.master.conflicts``).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from treble.core.facts import Fact
from treble.core.identifiers import TUID

#: The two relationship types that define the consolidation hierarchy.
DIRECT_PARENT_TYPE = "IS_DIRECTLY_CONSOLIDATED_BY"
ULTIMATE_PARENT_TYPE = "IS_ULTIMATELY_CONSOLIDATED_BY"

_RR_FIELD_PREFIX = "gleif:rr:"
_STATUS_SUFFIX = ":status"

#: A relationship is usable for graph traversal only when GLEIF still
#: asserts it as current. INACTIVE/NULL-status edges are kept in the fact
#: store (nothing is dropped) but excluded from the default resolution.
ACTIVE_STATUS = "ACTIVE"


class RelationshipEdge(BaseModel):
    """One evidence-carrying relationship between two LEI-keyed entities."""

    model_config = ConfigDict(frozen=True)

    child: TUID
    parent: TUID
    relationship_type: str
    status: str | None
    knowledge_from: datetime
    provenance_id: str


def edges_from_facts(facts: Iterable[Fact]) -> list[RelationshipEdge]:
    """Pair each relationship-value fact with its status fact.

    ``GleifRelationshipAdapter`` emits one value fact
    (``gleif:rr:<TYPE>`` -> end LEI) and one status fact
    (``gleif:rr:<TYPE>:status`` -> ACTIVE/INACTIVE/NULL) per RR-CDF record,
    sharing subject, provenance and knowledge date — that shared key is how
    they are recombined into one edge here.
    """
    all_facts = list(facts)
    statuses: dict[tuple[TUID, str, str, datetime], str | None] = {}
    for fact in all_facts:
        if not fact.field.startswith(_RR_FIELD_PREFIX) or not fact.field.endswith(_STATUS_SUFFIX):
            continue
        rel_type = fact.field.removeprefix(_RR_FIELD_PREFIX).removesuffix(_STATUS_SUFFIX)
        key = (fact.subject, rel_type, str(fact.provenance_id), fact.knowledge_from)
        statuses[key] = None if fact.value is None else str(fact.value)

    edges: list[RelationshipEdge] = []
    for fact in all_facts:
        if not fact.field.startswith(_RR_FIELD_PREFIX) or fact.field.endswith(_STATUS_SUFFIX):
            continue
        if fact.value in (None, ""):
            continue
        rel_type = fact.field.removeprefix(_RR_FIELD_PREFIX)
        key = (fact.subject, rel_type, str(fact.provenance_id), fact.knowledge_from)
        edges.append(
            RelationshipEdge(
                child=fact.subject,
                parent=TUID(f"lei:{str(fact.value).upper()}"),
                relationship_type=rel_type,
                status=statuses.get(key),
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return edges


def _visible(
    edges: Iterable[RelationshipEdge],
    *,
    relationship_type: str,
    as_of: datetime,
    active_only: bool,
) -> list[RelationshipEdge]:
    return [
        edge
        for edge in edges
        if edge.relationship_type == relationship_type
        and edge.knowledge_from <= as_of
        and (not active_only or edge.status == ACTIVE_STATUS)
    ]


def _resolve_parent(
    edges: Iterable[RelationshipEdge],
    lei: TUID,
    relationship_type: str,
    *,
    as_of: datetime,
    active_only: bool,
) -> TUID | None:
    """Resolve ``lei``'s parent of ``relationship_type`` as known at
    ``as_of`` (I2). Returns None when no evidence exists, or when the
    evidence is genuinely ambiguous — never a guess."""
    visible = _visible(
        edges, relationship_type=relationship_type, as_of=as_of, active_only=active_only
    )
    candidates = {edge.parent for edge in visible if edge.child == lei}
    return candidates.pop() if len(candidates) == 1 else None


def direct_parent(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, lei, DIRECT_PARENT_TYPE, as_of=as_of, active_only=active_only)


def ultimate_parent(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, lei, ULTIMATE_PARENT_TYPE, as_of=as_of, active_only=active_only)


def children(
    edges: Iterable[RelationshipEdge],
    lei: TUID,
    *,
    as_of: datetime,
    relationship_type: str = DIRECT_PARENT_TYPE,
    active_only: bool = True,
) -> list[TUID]:
    """Entities that assert ``lei`` as their parent of ``relationship_type``."""
    visible = _visible(
        edges, relationship_type=relationship_type, as_of=as_of, active_only=active_only
    )
    return sorted({edge.child for edge in visible if edge.parent == lei})


def conflicting_parents(
    edges: Iterable[RelationshipEdge],
    *,
    relationship_type: str,
    as_of: datetime,
    active_only: bool = True,
) -> list[tuple[TUID, list[TUID]]]:
    """Entities GLEIF has asserted more than one ``relationship_type``
    parent for, as of ``as_of`` — surfaced for review, never resolved by
    preferring one (spec §8.1.4: report disagreement, never pick a winner)."""
    by_child: dict[TUID, set[TUID]] = {}
    for edge in _visible(
        edges, relationship_type=relationship_type, as_of=as_of, active_only=active_only
    ):
        by_child.setdefault(edge.child, set()).add(edge.parent)
    return sorted(
        (child, sorted(parents)) for child, parents in by_child.items() if len(parents) > 1
    )
