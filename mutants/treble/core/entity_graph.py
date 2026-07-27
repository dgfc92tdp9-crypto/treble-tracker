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


from mutmut.mutation.trampoline import wrap_in_trampoline as _mutmut_mutated, MutantDict


class RelationshipEdge(BaseModel):
    """One evidence-carrying relationship between two LEI-keyed entities."""

    model_config = ConfigDict(frozen=True)

    child: TUID
    parent: TUID
    relationship_type: str
    status: str | None
    knowledge_from: datetime
    provenance_id: str
mutants_x_edges_from_facts__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_edges_from_facts__mutmut)
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


def x_edges_from_facts__mutmut_orig(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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


def x_edges_from_facts__mutmut_1(facts: Iterable[Fact]) -> list[RelationshipEdge]:
    """Pair each relationship-value fact with its status fact.

    ``GleifRelationshipAdapter`` emits one value fact
    (``gleif:rr:<TYPE>`` -> end LEI) and one status fact
    (``gleif:rr:<TYPE>:status`` -> ACTIVE/INACTIVE/NULL) per RR-CDF record,
    sharing subject, provenance and knowledge date — that shared key is how
    they are recombined into one edge here.
    """
    all_facts = None
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


def x_edges_from_facts__mutmut_2(facts: Iterable[Fact]) -> list[RelationshipEdge]:
    """Pair each relationship-value fact with its status fact.

    ``GleifRelationshipAdapter`` emits one value fact
    (``gleif:rr:<TYPE>`` -> end LEI) and one status fact
    (``gleif:rr:<TYPE>:status`` -> ACTIVE/INACTIVE/NULL) per RR-CDF record,
    sharing subject, provenance and knowledge date — that shared key is how
    they are recombined into one edge here.
    """
    all_facts = list(None)
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


def x_edges_from_facts__mutmut_3(facts: Iterable[Fact]) -> list[RelationshipEdge]:
    """Pair each relationship-value fact with its status fact.

    ``GleifRelationshipAdapter`` emits one value fact
    (``gleif:rr:<TYPE>`` -> end LEI) and one status fact
    (``gleif:rr:<TYPE>:status`` -> ACTIVE/INACTIVE/NULL) per RR-CDF record,
    sharing subject, provenance and knowledge date — that shared key is how
    they are recombined into one edge here.
    """
    all_facts = list(facts)
    statuses: dict[tuple[TUID, str, str, datetime], str | None] = None
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


def x_edges_from_facts__mutmut_4(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        if not fact.field.startswith(_RR_FIELD_PREFIX) and not fact.field.endswith(_STATUS_SUFFIX):
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


def x_edges_from_facts__mutmut_5(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        if fact.field.startswith(_RR_FIELD_PREFIX) or not fact.field.endswith(_STATUS_SUFFIX):
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


def x_edges_from_facts__mutmut_6(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        if not fact.field.startswith(None) or not fact.field.endswith(_STATUS_SUFFIX):
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


def x_edges_from_facts__mutmut_7(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        if not fact.field.startswith(_RR_FIELD_PREFIX) or fact.field.endswith(_STATUS_SUFFIX):
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


def x_edges_from_facts__mutmut_8(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        if not fact.field.startswith(_RR_FIELD_PREFIX) or not fact.field.endswith(None):
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


def x_edges_from_facts__mutmut_9(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
            break
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


def x_edges_from_facts__mutmut_10(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        rel_type = None
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


def x_edges_from_facts__mutmut_11(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        rel_type = fact.field.removeprefix(_RR_FIELD_PREFIX).removesuffix(None)
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


def x_edges_from_facts__mutmut_12(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        rel_type = fact.field.removeprefix(_RR_FIELD_PREFIX).removeprefix(_STATUS_SUFFIX)
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


def x_edges_from_facts__mutmut_13(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        rel_type = fact.field.removeprefix(None).removesuffix(_STATUS_SUFFIX)
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


def x_edges_from_facts__mutmut_14(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        rel_type = fact.field.removesuffix(_RR_FIELD_PREFIX).removesuffix(_STATUS_SUFFIX)
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


def x_edges_from_facts__mutmut_15(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        key = None
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


def x_edges_from_facts__mutmut_16(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        key = (fact.subject, rel_type, str(None), fact.knowledge_from)
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


def x_edges_from_facts__mutmut_17(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        statuses[key] = None

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


def x_edges_from_facts__mutmut_18(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        statuses[key] = None if fact.value is not None else str(fact.value)

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


def x_edges_from_facts__mutmut_19(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        statuses[key] = None if fact.value is None else str(None)

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


def x_edges_from_facts__mutmut_20(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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

    edges: list[RelationshipEdge] = None
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


def x_edges_from_facts__mutmut_21(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        if not fact.field.startswith(_RR_FIELD_PREFIX) and fact.field.endswith(_STATUS_SUFFIX):
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


def x_edges_from_facts__mutmut_22(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        if fact.field.startswith(_RR_FIELD_PREFIX) or fact.field.endswith(_STATUS_SUFFIX):
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


def x_edges_from_facts__mutmut_23(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        if not fact.field.startswith(None) or fact.field.endswith(_STATUS_SUFFIX):
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


def x_edges_from_facts__mutmut_24(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        if not fact.field.startswith(_RR_FIELD_PREFIX) or fact.field.endswith(None):
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


def x_edges_from_facts__mutmut_25(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
            break
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


def x_edges_from_facts__mutmut_26(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        if fact.value not in (None, ""):
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


def x_edges_from_facts__mutmut_27(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        if fact.value in (None, "XXXX"):
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


def x_edges_from_facts__mutmut_28(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
            break
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


def x_edges_from_facts__mutmut_29(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        rel_type = None
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


def x_edges_from_facts__mutmut_30(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        rel_type = fact.field.removeprefix(None)
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


def x_edges_from_facts__mutmut_31(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        rel_type = fact.field.removesuffix(_RR_FIELD_PREFIX)
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


def x_edges_from_facts__mutmut_32(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        key = None
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


def x_edges_from_facts__mutmut_33(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
        key = (fact.subject, rel_type, str(None), fact.knowledge_from)
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


def x_edges_from_facts__mutmut_34(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
            None
        )
    return edges


def x_edges_from_facts__mutmut_35(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
                child=None,
                parent=TUID(f"lei:{str(fact.value).upper()}"),
                relationship_type=rel_type,
                status=statuses.get(key),
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return edges


def x_edges_from_facts__mutmut_36(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
                parent=None,
                relationship_type=rel_type,
                status=statuses.get(key),
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return edges


def x_edges_from_facts__mutmut_37(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
                relationship_type=None,
                status=statuses.get(key),
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return edges


def x_edges_from_facts__mutmut_38(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
                status=None,
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return edges


def x_edges_from_facts__mutmut_39(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
                knowledge_from=None,
                provenance_id=str(fact.provenance_id),
            )
        )
    return edges


def x_edges_from_facts__mutmut_40(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
                provenance_id=None,
            )
        )
    return edges


def x_edges_from_facts__mutmut_41(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
                parent=TUID(f"lei:{str(fact.value).upper()}"),
                relationship_type=rel_type,
                status=statuses.get(key),
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return edges


def x_edges_from_facts__mutmut_42(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
                relationship_type=rel_type,
                status=statuses.get(key),
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return edges


def x_edges_from_facts__mutmut_43(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
                status=statuses.get(key),
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return edges


def x_edges_from_facts__mutmut_44(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return edges


def x_edges_from_facts__mutmut_45(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
                provenance_id=str(fact.provenance_id),
            )
        )
    return edges


def x_edges_from_facts__mutmut_46(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
                )
        )
    return edges


def x_edges_from_facts__mutmut_47(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
                parent=TUID(None),
                relationship_type=rel_type,
                status=statuses.get(key),
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return edges


def x_edges_from_facts__mutmut_48(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
                parent=TUID(f"lei:{str(fact.value).lower()}"),
                relationship_type=rel_type,
                status=statuses.get(key),
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return edges


def x_edges_from_facts__mutmut_49(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
                parent=TUID(f"lei:{str(None).upper()}"),
                relationship_type=rel_type,
                status=statuses.get(key),
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return edges


def x_edges_from_facts__mutmut_50(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
                status=statuses.get(None),
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return edges


def x_edges_from_facts__mutmut_51(facts: Iterable[Fact]) -> list[RelationshipEdge]:
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
                provenance_id=str(None),
            )
        )
    return edges

mutants_x_edges_from_facts__mutmut['_mutmut_orig'] = x_edges_from_facts__mutmut_orig # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_1'] = x_edges_from_facts__mutmut_1 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_2'] = x_edges_from_facts__mutmut_2 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_3'] = x_edges_from_facts__mutmut_3 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_4'] = x_edges_from_facts__mutmut_4 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_5'] = x_edges_from_facts__mutmut_5 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_6'] = x_edges_from_facts__mutmut_6 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_7'] = x_edges_from_facts__mutmut_7 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_8'] = x_edges_from_facts__mutmut_8 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_9'] = x_edges_from_facts__mutmut_9 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_10'] = x_edges_from_facts__mutmut_10 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_11'] = x_edges_from_facts__mutmut_11 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_12'] = x_edges_from_facts__mutmut_12 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_13'] = x_edges_from_facts__mutmut_13 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_14'] = x_edges_from_facts__mutmut_14 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_15'] = x_edges_from_facts__mutmut_15 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_16'] = x_edges_from_facts__mutmut_16 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_17'] = x_edges_from_facts__mutmut_17 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_18'] = x_edges_from_facts__mutmut_18 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_19'] = x_edges_from_facts__mutmut_19 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_20'] = x_edges_from_facts__mutmut_20 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_21'] = x_edges_from_facts__mutmut_21 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_22'] = x_edges_from_facts__mutmut_22 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_23'] = x_edges_from_facts__mutmut_23 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_24'] = x_edges_from_facts__mutmut_24 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_25'] = x_edges_from_facts__mutmut_25 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_26'] = x_edges_from_facts__mutmut_26 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_27'] = x_edges_from_facts__mutmut_27 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_28'] = x_edges_from_facts__mutmut_28 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_29'] = x_edges_from_facts__mutmut_29 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_30'] = x_edges_from_facts__mutmut_30 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_31'] = x_edges_from_facts__mutmut_31 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_32'] = x_edges_from_facts__mutmut_32 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_33'] = x_edges_from_facts__mutmut_33 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_34'] = x_edges_from_facts__mutmut_34 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_35'] = x_edges_from_facts__mutmut_35 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_36'] = x_edges_from_facts__mutmut_36 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_37'] = x_edges_from_facts__mutmut_37 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_38'] = x_edges_from_facts__mutmut_38 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_39'] = x_edges_from_facts__mutmut_39 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_40'] = x_edges_from_facts__mutmut_40 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_41'] = x_edges_from_facts__mutmut_41 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_42'] = x_edges_from_facts__mutmut_42 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_43'] = x_edges_from_facts__mutmut_43 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_44'] = x_edges_from_facts__mutmut_44 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_45'] = x_edges_from_facts__mutmut_45 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_46'] = x_edges_from_facts__mutmut_46 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_47'] = x_edges_from_facts__mutmut_47 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_48'] = x_edges_from_facts__mutmut_48 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_49'] = x_edges_from_facts__mutmut_49 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_50'] = x_edges_from_facts__mutmut_50 # type: ignore # mutmut generated
mutants_x_edges_from_facts__mutmut['x_edges_from_facts__mutmut_51'] = x_edges_from_facts__mutmut_51 # type: ignore # mutmut generated
mutants_x__visible__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x__visible__mutmut)
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


def x__visible__mutmut_orig(
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


def x__visible__mutmut_1(
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
        and edge.knowledge_from <= as_of or (not active_only or edge.status == ACTIVE_STATUS)
    ]


def x__visible__mutmut_2(
    edges: Iterable[RelationshipEdge],
    *,
    relationship_type: str,
    as_of: datetime,
    active_only: bool,
) -> list[RelationshipEdge]:
    return [
        edge
        for edge in edges
        if edge.relationship_type == relationship_type or edge.knowledge_from <= as_of
        and (not active_only or edge.status == ACTIVE_STATUS)
    ]


def x__visible__mutmut_3(
    edges: Iterable[RelationshipEdge],
    *,
    relationship_type: str,
    as_of: datetime,
    active_only: bool,
) -> list[RelationshipEdge]:
    return [
        edge
        for edge in edges
        if edge.relationship_type != relationship_type
        and edge.knowledge_from <= as_of
        and (not active_only or edge.status == ACTIVE_STATUS)
    ]


def x__visible__mutmut_4(
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
        and edge.knowledge_from < as_of
        and (not active_only or edge.status == ACTIVE_STATUS)
    ]


def x__visible__mutmut_5(
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
        and (not active_only and edge.status == ACTIVE_STATUS)
    ]


def x__visible__mutmut_6(
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
        and (active_only or edge.status == ACTIVE_STATUS)
    ]


def x__visible__mutmut_7(
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
        and (not active_only or edge.status != ACTIVE_STATUS)
    ]

mutants_x__visible__mutmut['_mutmut_orig'] = x__visible__mutmut_orig # type: ignore # mutmut generated
mutants_x__visible__mutmut['x__visible__mutmut_1'] = x__visible__mutmut_1 # type: ignore # mutmut generated
mutants_x__visible__mutmut['x__visible__mutmut_2'] = x__visible__mutmut_2 # type: ignore # mutmut generated
mutants_x__visible__mutmut['x__visible__mutmut_3'] = x__visible__mutmut_3 # type: ignore # mutmut generated
mutants_x__visible__mutmut['x__visible__mutmut_4'] = x__visible__mutmut_4 # type: ignore # mutmut generated
mutants_x__visible__mutmut['x__visible__mutmut_5'] = x__visible__mutmut_5 # type: ignore # mutmut generated
mutants_x__visible__mutmut['x__visible__mutmut_6'] = x__visible__mutmut_6 # type: ignore # mutmut generated
mutants_x__visible__mutmut['x__visible__mutmut_7'] = x__visible__mutmut_7 # type: ignore # mutmut generated
mutants_x__resolve_parent__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x__resolve_parent__mutmut)
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


def x__resolve_parent__mutmut_orig(
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


def x__resolve_parent__mutmut_1(
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
    visible = None
    candidates = {edge.parent for edge in visible if edge.child == lei}
    return candidates.pop() if len(candidates) == 1 else None


def x__resolve_parent__mutmut_2(
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
        None, relationship_type=relationship_type, as_of=as_of, active_only=active_only
    )
    candidates = {edge.parent for edge in visible if edge.child == lei}
    return candidates.pop() if len(candidates) == 1 else None


def x__resolve_parent__mutmut_3(
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
        edges, relationship_type=None, as_of=as_of, active_only=active_only
    )
    candidates = {edge.parent for edge in visible if edge.child == lei}
    return candidates.pop() if len(candidates) == 1 else None


def x__resolve_parent__mutmut_4(
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
        edges, relationship_type=relationship_type, as_of=None, active_only=active_only
    )
    candidates = {edge.parent for edge in visible if edge.child == lei}
    return candidates.pop() if len(candidates) == 1 else None


def x__resolve_parent__mutmut_5(
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
        edges, relationship_type=relationship_type, as_of=as_of, active_only=None
    )
    candidates = {edge.parent for edge in visible if edge.child == lei}
    return candidates.pop() if len(candidates) == 1 else None


def x__resolve_parent__mutmut_6(
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
        relationship_type=relationship_type, as_of=as_of, active_only=active_only
    )
    candidates = {edge.parent for edge in visible if edge.child == lei}
    return candidates.pop() if len(candidates) == 1 else None


def x__resolve_parent__mutmut_7(
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
        edges, as_of=as_of, active_only=active_only
    )
    candidates = {edge.parent for edge in visible if edge.child == lei}
    return candidates.pop() if len(candidates) == 1 else None


def x__resolve_parent__mutmut_8(
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
        edges, relationship_type=relationship_type, active_only=active_only
    )
    candidates = {edge.parent for edge in visible if edge.child == lei}
    return candidates.pop() if len(candidates) == 1 else None


def x__resolve_parent__mutmut_9(
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
        edges, relationship_type=relationship_type, as_of=as_of, )
    candidates = {edge.parent for edge in visible if edge.child == lei}
    return candidates.pop() if len(candidates) == 1 else None


def x__resolve_parent__mutmut_10(
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
    candidates = None
    return candidates.pop() if len(candidates) == 1 else None


def x__resolve_parent__mutmut_11(
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
    candidates = {edge.parent for edge in visible if edge.child != lei}
    return candidates.pop() if len(candidates) == 1 else None


def x__resolve_parent__mutmut_12(
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
    return candidates.pop() if len(candidates) != 1 else None


def x__resolve_parent__mutmut_13(
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
    return candidates.pop() if len(candidates) == 2 else None

mutants_x__resolve_parent__mutmut['_mutmut_orig'] = x__resolve_parent__mutmut_orig # type: ignore # mutmut generated
mutants_x__resolve_parent__mutmut['x__resolve_parent__mutmut_1'] = x__resolve_parent__mutmut_1 # type: ignore # mutmut generated
mutants_x__resolve_parent__mutmut['x__resolve_parent__mutmut_2'] = x__resolve_parent__mutmut_2 # type: ignore # mutmut generated
mutants_x__resolve_parent__mutmut['x__resolve_parent__mutmut_3'] = x__resolve_parent__mutmut_3 # type: ignore # mutmut generated
mutants_x__resolve_parent__mutmut['x__resolve_parent__mutmut_4'] = x__resolve_parent__mutmut_4 # type: ignore # mutmut generated
mutants_x__resolve_parent__mutmut['x__resolve_parent__mutmut_5'] = x__resolve_parent__mutmut_5 # type: ignore # mutmut generated
mutants_x__resolve_parent__mutmut['x__resolve_parent__mutmut_6'] = x__resolve_parent__mutmut_6 # type: ignore # mutmut generated
mutants_x__resolve_parent__mutmut['x__resolve_parent__mutmut_7'] = x__resolve_parent__mutmut_7 # type: ignore # mutmut generated
mutants_x__resolve_parent__mutmut['x__resolve_parent__mutmut_8'] = x__resolve_parent__mutmut_8 # type: ignore # mutmut generated
mutants_x__resolve_parent__mutmut['x__resolve_parent__mutmut_9'] = x__resolve_parent__mutmut_9 # type: ignore # mutmut generated
mutants_x__resolve_parent__mutmut['x__resolve_parent__mutmut_10'] = x__resolve_parent__mutmut_10 # type: ignore # mutmut generated
mutants_x__resolve_parent__mutmut['x__resolve_parent__mutmut_11'] = x__resolve_parent__mutmut_11 # type: ignore # mutmut generated
mutants_x__resolve_parent__mutmut['x__resolve_parent__mutmut_12'] = x__resolve_parent__mutmut_12 # type: ignore # mutmut generated
mutants_x__resolve_parent__mutmut['x__resolve_parent__mutmut_13'] = x__resolve_parent__mutmut_13 # type: ignore # mutmut generated
mutants_x_direct_parent__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_direct_parent__mutmut)
def direct_parent(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, lei, DIRECT_PARENT_TYPE, as_of=as_of, active_only=active_only)


def x_direct_parent__mutmut_orig(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, lei, DIRECT_PARENT_TYPE, as_of=as_of, active_only=active_only)


def x_direct_parent__mutmut_1(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = False
) -> TUID | None:
    return _resolve_parent(edges, lei, DIRECT_PARENT_TYPE, as_of=as_of, active_only=active_only)


def x_direct_parent__mutmut_2(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(None, lei, DIRECT_PARENT_TYPE, as_of=as_of, active_only=active_only)


def x_direct_parent__mutmut_3(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, None, DIRECT_PARENT_TYPE, as_of=as_of, active_only=active_only)


def x_direct_parent__mutmut_4(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, lei, None, as_of=as_of, active_only=active_only)


def x_direct_parent__mutmut_5(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, lei, DIRECT_PARENT_TYPE, as_of=None, active_only=active_only)


def x_direct_parent__mutmut_6(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, lei, DIRECT_PARENT_TYPE, as_of=as_of, active_only=None)


def x_direct_parent__mutmut_7(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(lei, DIRECT_PARENT_TYPE, as_of=as_of, active_only=active_only)


def x_direct_parent__mutmut_8(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, DIRECT_PARENT_TYPE, as_of=as_of, active_only=active_only)


def x_direct_parent__mutmut_9(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, lei, as_of=as_of, active_only=active_only)


def x_direct_parent__mutmut_10(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, lei, DIRECT_PARENT_TYPE, active_only=active_only)


def x_direct_parent__mutmut_11(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, lei, DIRECT_PARENT_TYPE, as_of=as_of, )

mutants_x_direct_parent__mutmut['_mutmut_orig'] = x_direct_parent__mutmut_orig # type: ignore # mutmut generated
mutants_x_direct_parent__mutmut['x_direct_parent__mutmut_1'] = x_direct_parent__mutmut_1 # type: ignore # mutmut generated
mutants_x_direct_parent__mutmut['x_direct_parent__mutmut_2'] = x_direct_parent__mutmut_2 # type: ignore # mutmut generated
mutants_x_direct_parent__mutmut['x_direct_parent__mutmut_3'] = x_direct_parent__mutmut_3 # type: ignore # mutmut generated
mutants_x_direct_parent__mutmut['x_direct_parent__mutmut_4'] = x_direct_parent__mutmut_4 # type: ignore # mutmut generated
mutants_x_direct_parent__mutmut['x_direct_parent__mutmut_5'] = x_direct_parent__mutmut_5 # type: ignore # mutmut generated
mutants_x_direct_parent__mutmut['x_direct_parent__mutmut_6'] = x_direct_parent__mutmut_6 # type: ignore # mutmut generated
mutants_x_direct_parent__mutmut['x_direct_parent__mutmut_7'] = x_direct_parent__mutmut_7 # type: ignore # mutmut generated
mutants_x_direct_parent__mutmut['x_direct_parent__mutmut_8'] = x_direct_parent__mutmut_8 # type: ignore # mutmut generated
mutants_x_direct_parent__mutmut['x_direct_parent__mutmut_9'] = x_direct_parent__mutmut_9 # type: ignore # mutmut generated
mutants_x_direct_parent__mutmut['x_direct_parent__mutmut_10'] = x_direct_parent__mutmut_10 # type: ignore # mutmut generated
mutants_x_direct_parent__mutmut['x_direct_parent__mutmut_11'] = x_direct_parent__mutmut_11 # type: ignore # mutmut generated
mutants_x_ultimate_parent__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_ultimate_parent__mutmut)
def ultimate_parent(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, lei, ULTIMATE_PARENT_TYPE, as_of=as_of, active_only=active_only)


def x_ultimate_parent__mutmut_orig(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, lei, ULTIMATE_PARENT_TYPE, as_of=as_of, active_only=active_only)


def x_ultimate_parent__mutmut_1(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = False
) -> TUID | None:
    return _resolve_parent(edges, lei, ULTIMATE_PARENT_TYPE, as_of=as_of, active_only=active_only)


def x_ultimate_parent__mutmut_2(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(None, lei, ULTIMATE_PARENT_TYPE, as_of=as_of, active_only=active_only)


def x_ultimate_parent__mutmut_3(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, None, ULTIMATE_PARENT_TYPE, as_of=as_of, active_only=active_only)


def x_ultimate_parent__mutmut_4(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, lei, None, as_of=as_of, active_only=active_only)


def x_ultimate_parent__mutmut_5(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, lei, ULTIMATE_PARENT_TYPE, as_of=None, active_only=active_only)


def x_ultimate_parent__mutmut_6(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, lei, ULTIMATE_PARENT_TYPE, as_of=as_of, active_only=None)


def x_ultimate_parent__mutmut_7(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(lei, ULTIMATE_PARENT_TYPE, as_of=as_of, active_only=active_only)


def x_ultimate_parent__mutmut_8(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, ULTIMATE_PARENT_TYPE, as_of=as_of, active_only=active_only)


def x_ultimate_parent__mutmut_9(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, lei, as_of=as_of, active_only=active_only)


def x_ultimate_parent__mutmut_10(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, lei, ULTIMATE_PARENT_TYPE, active_only=active_only)


def x_ultimate_parent__mutmut_11(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return _resolve_parent(edges, lei, ULTIMATE_PARENT_TYPE, as_of=as_of, )

mutants_x_ultimate_parent__mutmut['_mutmut_orig'] = x_ultimate_parent__mutmut_orig # type: ignore # mutmut generated
mutants_x_ultimate_parent__mutmut['x_ultimate_parent__mutmut_1'] = x_ultimate_parent__mutmut_1 # type: ignore # mutmut generated
mutants_x_ultimate_parent__mutmut['x_ultimate_parent__mutmut_2'] = x_ultimate_parent__mutmut_2 # type: ignore # mutmut generated
mutants_x_ultimate_parent__mutmut['x_ultimate_parent__mutmut_3'] = x_ultimate_parent__mutmut_3 # type: ignore # mutmut generated
mutants_x_ultimate_parent__mutmut['x_ultimate_parent__mutmut_4'] = x_ultimate_parent__mutmut_4 # type: ignore # mutmut generated
mutants_x_ultimate_parent__mutmut['x_ultimate_parent__mutmut_5'] = x_ultimate_parent__mutmut_5 # type: ignore # mutmut generated
mutants_x_ultimate_parent__mutmut['x_ultimate_parent__mutmut_6'] = x_ultimate_parent__mutmut_6 # type: ignore # mutmut generated
mutants_x_ultimate_parent__mutmut['x_ultimate_parent__mutmut_7'] = x_ultimate_parent__mutmut_7 # type: ignore # mutmut generated
mutants_x_ultimate_parent__mutmut['x_ultimate_parent__mutmut_8'] = x_ultimate_parent__mutmut_8 # type: ignore # mutmut generated
mutants_x_ultimate_parent__mutmut['x_ultimate_parent__mutmut_9'] = x_ultimate_parent__mutmut_9 # type: ignore # mutmut generated
mutants_x_ultimate_parent__mutmut['x_ultimate_parent__mutmut_10'] = x_ultimate_parent__mutmut_10 # type: ignore # mutmut generated
mutants_x_ultimate_parent__mutmut['x_ultimate_parent__mutmut_11'] = x_ultimate_parent__mutmut_11 # type: ignore # mutmut generated
mutants_x_children__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_children__mutmut)
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


def x_children__mutmut_orig(
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


def x_children__mutmut_1(
    edges: Iterable[RelationshipEdge],
    lei: TUID,
    *,
    as_of: datetime,
    relationship_type: str = DIRECT_PARENT_TYPE,
    active_only: bool = False,
) -> list[TUID]:
    """Entities that assert ``lei`` as their parent of ``relationship_type``."""
    visible = _visible(
        edges, relationship_type=relationship_type, as_of=as_of, active_only=active_only
    )
    return sorted({edge.child for edge in visible if edge.parent == lei})


def x_children__mutmut_2(
    edges: Iterable[RelationshipEdge],
    lei: TUID,
    *,
    as_of: datetime,
    relationship_type: str = DIRECT_PARENT_TYPE,
    active_only: bool = True,
) -> list[TUID]:
    """Entities that assert ``lei`` as their parent of ``relationship_type``."""
    visible = None
    return sorted({edge.child for edge in visible if edge.parent == lei})


def x_children__mutmut_3(
    edges: Iterable[RelationshipEdge],
    lei: TUID,
    *,
    as_of: datetime,
    relationship_type: str = DIRECT_PARENT_TYPE,
    active_only: bool = True,
) -> list[TUID]:
    """Entities that assert ``lei`` as their parent of ``relationship_type``."""
    visible = _visible(
        None, relationship_type=relationship_type, as_of=as_of, active_only=active_only
    )
    return sorted({edge.child for edge in visible if edge.parent == lei})


def x_children__mutmut_4(
    edges: Iterable[RelationshipEdge],
    lei: TUID,
    *,
    as_of: datetime,
    relationship_type: str = DIRECT_PARENT_TYPE,
    active_only: bool = True,
) -> list[TUID]:
    """Entities that assert ``lei`` as their parent of ``relationship_type``."""
    visible = _visible(
        edges, relationship_type=None, as_of=as_of, active_only=active_only
    )
    return sorted({edge.child for edge in visible if edge.parent == lei})


def x_children__mutmut_5(
    edges: Iterable[RelationshipEdge],
    lei: TUID,
    *,
    as_of: datetime,
    relationship_type: str = DIRECT_PARENT_TYPE,
    active_only: bool = True,
) -> list[TUID]:
    """Entities that assert ``lei`` as their parent of ``relationship_type``."""
    visible = _visible(
        edges, relationship_type=relationship_type, as_of=None, active_only=active_only
    )
    return sorted({edge.child for edge in visible if edge.parent == lei})


def x_children__mutmut_6(
    edges: Iterable[RelationshipEdge],
    lei: TUID,
    *,
    as_of: datetime,
    relationship_type: str = DIRECT_PARENT_TYPE,
    active_only: bool = True,
) -> list[TUID]:
    """Entities that assert ``lei`` as their parent of ``relationship_type``."""
    visible = _visible(
        edges, relationship_type=relationship_type, as_of=as_of, active_only=None
    )
    return sorted({edge.child for edge in visible if edge.parent == lei})


def x_children__mutmut_7(
    edges: Iterable[RelationshipEdge],
    lei: TUID,
    *,
    as_of: datetime,
    relationship_type: str = DIRECT_PARENT_TYPE,
    active_only: bool = True,
) -> list[TUID]:
    """Entities that assert ``lei`` as their parent of ``relationship_type``."""
    visible = _visible(
        relationship_type=relationship_type, as_of=as_of, active_only=active_only
    )
    return sorted({edge.child for edge in visible if edge.parent == lei})


def x_children__mutmut_8(
    edges: Iterable[RelationshipEdge],
    lei: TUID,
    *,
    as_of: datetime,
    relationship_type: str = DIRECT_PARENT_TYPE,
    active_only: bool = True,
) -> list[TUID]:
    """Entities that assert ``lei`` as their parent of ``relationship_type``."""
    visible = _visible(
        edges, as_of=as_of, active_only=active_only
    )
    return sorted({edge.child for edge in visible if edge.parent == lei})


def x_children__mutmut_9(
    edges: Iterable[RelationshipEdge],
    lei: TUID,
    *,
    as_of: datetime,
    relationship_type: str = DIRECT_PARENT_TYPE,
    active_only: bool = True,
) -> list[TUID]:
    """Entities that assert ``lei`` as their parent of ``relationship_type``."""
    visible = _visible(
        edges, relationship_type=relationship_type, active_only=active_only
    )
    return sorted({edge.child for edge in visible if edge.parent == lei})


def x_children__mutmut_10(
    edges: Iterable[RelationshipEdge],
    lei: TUID,
    *,
    as_of: datetime,
    relationship_type: str = DIRECT_PARENT_TYPE,
    active_only: bool = True,
) -> list[TUID]:
    """Entities that assert ``lei`` as their parent of ``relationship_type``."""
    visible = _visible(
        edges, relationship_type=relationship_type, as_of=as_of, )
    return sorted({edge.child for edge in visible if edge.parent == lei})


def x_children__mutmut_11(
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
    return sorted(None)


def x_children__mutmut_12(
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
    return sorted({edge.child for edge in visible if edge.parent != lei})

mutants_x_children__mutmut['_mutmut_orig'] = x_children__mutmut_orig # type: ignore # mutmut generated
mutants_x_children__mutmut['x_children__mutmut_1'] = x_children__mutmut_1 # type: ignore # mutmut generated
mutants_x_children__mutmut['x_children__mutmut_2'] = x_children__mutmut_2 # type: ignore # mutmut generated
mutants_x_children__mutmut['x_children__mutmut_3'] = x_children__mutmut_3 # type: ignore # mutmut generated
mutants_x_children__mutmut['x_children__mutmut_4'] = x_children__mutmut_4 # type: ignore # mutmut generated
mutants_x_children__mutmut['x_children__mutmut_5'] = x_children__mutmut_5 # type: ignore # mutmut generated
mutants_x_children__mutmut['x_children__mutmut_6'] = x_children__mutmut_6 # type: ignore # mutmut generated
mutants_x_children__mutmut['x_children__mutmut_7'] = x_children__mutmut_7 # type: ignore # mutmut generated
mutants_x_children__mutmut['x_children__mutmut_8'] = x_children__mutmut_8 # type: ignore # mutmut generated
mutants_x_children__mutmut['x_children__mutmut_9'] = x_children__mutmut_9 # type: ignore # mutmut generated
mutants_x_children__mutmut['x_children__mutmut_10'] = x_children__mutmut_10 # type: ignore # mutmut generated
mutants_x_children__mutmut['x_children__mutmut_11'] = x_children__mutmut_11 # type: ignore # mutmut generated
mutants_x_children__mutmut['x_children__mutmut_12'] = x_children__mutmut_12 # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_conflicting_parents__mutmut)
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


def x_conflicting_parents__mutmut_orig(
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


def x_conflicting_parents__mutmut_1(
    edges: Iterable[RelationshipEdge],
    *,
    relationship_type: str,
    as_of: datetime,
    active_only: bool = False,
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


def x_conflicting_parents__mutmut_2(
    edges: Iterable[RelationshipEdge],
    *,
    relationship_type: str,
    as_of: datetime,
    active_only: bool = True,
) -> list[tuple[TUID, list[TUID]]]:
    """Entities GLEIF has asserted more than one ``relationship_type``
    parent for, as of ``as_of`` — surfaced for review, never resolved by
    preferring one (spec §8.1.4: report disagreement, never pick a winner)."""
    by_child: dict[TUID, set[TUID]] = None
    for edge in _visible(
        edges, relationship_type=relationship_type, as_of=as_of, active_only=active_only
    ):
        by_child.setdefault(edge.child, set()).add(edge.parent)
    return sorted(
        (child, sorted(parents)) for child, parents in by_child.items() if len(parents) > 1
    )


def x_conflicting_parents__mutmut_3(
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
        None, relationship_type=relationship_type, as_of=as_of, active_only=active_only
    ):
        by_child.setdefault(edge.child, set()).add(edge.parent)
    return sorted(
        (child, sorted(parents)) for child, parents in by_child.items() if len(parents) > 1
    )


def x_conflicting_parents__mutmut_4(
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
        edges, relationship_type=None, as_of=as_of, active_only=active_only
    ):
        by_child.setdefault(edge.child, set()).add(edge.parent)
    return sorted(
        (child, sorted(parents)) for child, parents in by_child.items() if len(parents) > 1
    )


def x_conflicting_parents__mutmut_5(
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
        edges, relationship_type=relationship_type, as_of=None, active_only=active_only
    ):
        by_child.setdefault(edge.child, set()).add(edge.parent)
    return sorted(
        (child, sorted(parents)) for child, parents in by_child.items() if len(parents) > 1
    )


def x_conflicting_parents__mutmut_6(
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
        edges, relationship_type=relationship_type, as_of=as_of, active_only=None
    ):
        by_child.setdefault(edge.child, set()).add(edge.parent)
    return sorted(
        (child, sorted(parents)) for child, parents in by_child.items() if len(parents) > 1
    )


def x_conflicting_parents__mutmut_7(
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
        relationship_type=relationship_type, as_of=as_of, active_only=active_only
    ):
        by_child.setdefault(edge.child, set()).add(edge.parent)
    return sorted(
        (child, sorted(parents)) for child, parents in by_child.items() if len(parents) > 1
    )


def x_conflicting_parents__mutmut_8(
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
        edges, as_of=as_of, active_only=active_only
    ):
        by_child.setdefault(edge.child, set()).add(edge.parent)
    return sorted(
        (child, sorted(parents)) for child, parents in by_child.items() if len(parents) > 1
    )


def x_conflicting_parents__mutmut_9(
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
        edges, relationship_type=relationship_type, active_only=active_only
    ):
        by_child.setdefault(edge.child, set()).add(edge.parent)
    return sorted(
        (child, sorted(parents)) for child, parents in by_child.items() if len(parents) > 1
    )


def x_conflicting_parents__mutmut_10(
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
        edges, relationship_type=relationship_type, as_of=as_of, ):
        by_child.setdefault(edge.child, set()).add(edge.parent)
    return sorted(
        (child, sorted(parents)) for child, parents in by_child.items() if len(parents) > 1
    )


def x_conflicting_parents__mutmut_11(
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
        by_child.setdefault(edge.child, set()).add(None)
    return sorted(
        (child, sorted(parents)) for child, parents in by_child.items() if len(parents) > 1
    )


def x_conflicting_parents__mutmut_12(
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
        by_child.setdefault(None, set()).add(edge.parent)
    return sorted(
        (child, sorted(parents)) for child, parents in by_child.items() if len(parents) > 1
    )


def x_conflicting_parents__mutmut_13(
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
        by_child.setdefault(edge.child, None).add(edge.parent)
    return sorted(
        (child, sorted(parents)) for child, parents in by_child.items() if len(parents) > 1
    )


def x_conflicting_parents__mutmut_14(
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
        by_child.setdefault(set()).add(edge.parent)
    return sorted(
        (child, sorted(parents)) for child, parents in by_child.items() if len(parents) > 1
    )


def x_conflicting_parents__mutmut_15(
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
        by_child.setdefault(edge.child, ).add(edge.parent)
    return sorted(
        (child, sorted(parents)) for child, parents in by_child.items() if len(parents) > 1
    )


def x_conflicting_parents__mutmut_16(
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
        None
    )


def x_conflicting_parents__mutmut_17(
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
        (child, sorted(None)) for child, parents in by_child.items() if len(parents) > 1
    )


def x_conflicting_parents__mutmut_18(
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
        (child, sorted(parents)) for child, parents in by_child.items() if len(parents) >= 1
    )


def x_conflicting_parents__mutmut_19(
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
        (child, sorted(parents)) for child, parents in by_child.items() if len(parents) > 2
    )

mutants_x_conflicting_parents__mutmut['_mutmut_orig'] = x_conflicting_parents__mutmut_orig # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut['x_conflicting_parents__mutmut_1'] = x_conflicting_parents__mutmut_1 # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut['x_conflicting_parents__mutmut_2'] = x_conflicting_parents__mutmut_2 # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut['x_conflicting_parents__mutmut_3'] = x_conflicting_parents__mutmut_3 # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut['x_conflicting_parents__mutmut_4'] = x_conflicting_parents__mutmut_4 # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut['x_conflicting_parents__mutmut_5'] = x_conflicting_parents__mutmut_5 # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut['x_conflicting_parents__mutmut_6'] = x_conflicting_parents__mutmut_6 # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut['x_conflicting_parents__mutmut_7'] = x_conflicting_parents__mutmut_7 # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut['x_conflicting_parents__mutmut_8'] = x_conflicting_parents__mutmut_8 # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut['x_conflicting_parents__mutmut_9'] = x_conflicting_parents__mutmut_9 # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut['x_conflicting_parents__mutmut_10'] = x_conflicting_parents__mutmut_10 # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut['x_conflicting_parents__mutmut_11'] = x_conflicting_parents__mutmut_11 # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut['x_conflicting_parents__mutmut_12'] = x_conflicting_parents__mutmut_12 # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut['x_conflicting_parents__mutmut_13'] = x_conflicting_parents__mutmut_13 # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut['x_conflicting_parents__mutmut_14'] = x_conflicting_parents__mutmut_14 # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut['x_conflicting_parents__mutmut_15'] = x_conflicting_parents__mutmut_15 # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut['x_conflicting_parents__mutmut_16'] = x_conflicting_parents__mutmut_16 # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut['x_conflicting_parents__mutmut_17'] = x_conflicting_parents__mutmut_17 # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut['x_conflicting_parents__mutmut_18'] = x_conflicting_parents__mutmut_18 # type: ignore # mutmut generated
mutants_x_conflicting_parents__mutmut['x_conflicting_parents__mutmut_19'] = x_conflicting_parents__mutmut_19 # type: ignore # mutmut generated
