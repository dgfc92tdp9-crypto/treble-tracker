"""The entity graph, reachable from a service (spec §9.5).

`core/entity_graph.py` has held the edge model and the parent/child walks
since WP7 and nothing called it — partly because until the GLEIF
relationship backfill there was no graph to walk. There is now: 1,326,770
relationship facts across 373,125 LEI subjects.

**Ancestry is cheap; descent is not, and the difference is in the store's
shape.** Asking who owns an entity reads that entity's own facts — one
subject, a handful of edges. Asking who an entity *owns* means finding
every subject whose parent fact points at it, and facts are indexed by
subject rather than by value. :func:`ancestry_of` is the cheap direction
and :func:`children_of` says plainly that it scans, so a screen author
meets the cost before a user does rather than after.

**Direct and ultimate parent are asked separately, never derived.**
`core/entity_graph.py` deliberately does not walk direct-parent edges to
infer an ultimate parent, because GLEIF's own ultimate-parent assertion may
reflect information the direct chain does not carry — an accounting
consolidation that skips an intermediate holding company, for instance.
This passes both through as GLEIF stated them, and where they differ that
difference is the answer rather than an inconsistency to reconcile.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from treble.core.entity_graph import (
    DIRECT_PARENT_TYPE,
    ULTIMATE_PARENT_TYPE,
    RelationshipEdge,
    direct_parent,
    edges_from_facts,
    ultimate_parent,
)
from treble.core.identifiers import TUID
from treble.store.duck import DuckStore


class EntityUnknownError(RuntimeError):
    """No relationship evidence for this entity, with what was looked for."""


@dataclass(frozen=True)
class Ancestry:
    """Who owns an entity, as GLEIF asserts it."""

    subject: str
    direct_parent: TUID | None
    ultimate_parent: TUID | None
    #: Every edge the entity itself asserts, so `SPTR` can show the chain
    #: rather than only its endpoints.
    edges: tuple[RelationshipEdge, ...]

    @property
    def parents_agree(self) -> bool:
        """Whether the direct and ultimate parents are the same entity.

        They often are, and where they are not that is information rather
        than a fault: GLEIF's ultimate-parent assertion may skip an
        intermediate holding company the direct edge names.
        """
        return self.direct_parent == self.ultimate_parent


def ancestry_of(store: DuckStore, subject: TUID, *, as_of: datetime) -> Ancestry:
    """Direct and ultimate parent for one entity, point-in-time.

    Both are read from the entity's own asserted edges. Neither is derived
    from the other — see the module docstring on why walking the direct
    chain to infer an ultimate parent would answer a different question
    from the one GLEIF answers.
    """
    facts = store.subject_facts(subject, as_of=as_of)
    edges = edges_from_facts(facts)
    if not edges:
        raise EntityUnknownError(
            f"{subject}: no GLEIF relationship facts as of {as_of.date()}. An entity with "
            "no parent and an entity nobody has filed a relationship for are different, "
            "and this is the second"
        )
    return Ancestry(
        subject=str(subject),
        direct_parent=direct_parent(edges, subject, as_of=as_of),
        ultimate_parent=ultimate_parent(edges, subject, as_of=as_of),
        edges=tuple(edges),
    )


def children_of(
    store: DuckStore,
    parent: TUID,
    *,
    as_of: datetime,
    relationship_type: str = DIRECT_PARENT_TYPE,
) -> tuple[TUID, ...]:
    """Entities asserting `parent` as their parent of this type.

    Answered by one reverse query rather than by walking subjects. The
    first version scanned every LEI subject and documented it as "this
    scans"; the second refused above a threshold, having measured that the
    live store's 373,125 subjects would make a screen appear to hang.

    Both were solving the wrong problem. The rows sit in one table and the
    store can answer this directly — the expense was a Python loop, not a
    missing index. `DuckStore.subjects_with_value` is that query, and the
    threshold is gone because there is nothing left to guard against.

    A child asserts its parent on its *own* subject, so the subjects this
    returns are the children and the value matched is the parent.
    """
    lei = str(parent).removeprefix("lei:")
    return tuple(store.subjects_with_value(f"gleif:rr:{relationship_type}", lei, as_of=as_of))


__all__ = [
    "DIRECT_PARENT_TYPE",
    "ULTIMATE_PARENT_TYPE",
    "Ancestry",
    "EntityUnknownError",
    "ancestry_of",
    "children_of",
]
