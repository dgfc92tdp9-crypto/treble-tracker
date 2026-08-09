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
    children,
    direct_parent,
    edges_from_facts,
    ultimate_parent,
)
from treble.core.identifiers import TUID
from treble.store.duck import DuckStore

#: Largest LEI universe `children_of` will scan. Past this it refuses:
#: there is no parent-to-child index, so the answer costs one read per
#: subject, and the live store holds 373,125 of them.
MAX_SCAN_SUBJECTS = 5_000


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
    max_universe: int = MAX_SCAN_SUBJECTS,
) -> tuple[TUID, ...]:
    """Entities asserting `parent` as their parent of this type.

    **Refuses at scale rather than scanning.** Facts are keyed by subject,
    and a child asserts its parent on its own subject, so there is no index
    from parent to child. Answering this means reading every LEI subject in
    the store — 373,125 of them after the GLEIF backfill — one at a time.

    The first version of this function did exactly that and documented it
    as "this scans", which understated it: a screen calling it would appear
    to hang, and a docstring is not a substitute for an operation that
    finishes. It now refuses above `max_universe` and names the missing
    index, so the cost is met as an error a developer reads rather than as
    a spinner a user watches.

    Below that threshold — a seeded install, a test, a filtered universe —
    it answers normally, because there the scan is cheap and the answer is
    real.
    """
    subjects = store.subjects_with_prefix("lei:", as_of=as_of)
    if len(subjects) > max_universe:
        raise EntityUnknownError(
            f"{len(subjects)} LEI subjects is past the {max_universe} this can scan. "
            "Facts are keyed by subject and a child asserts its own parent, so there is "
            "no parent-to-child index; building one is the fix, not waiting longer"
        )
    found: list[TUID] = []
    for subject in subjects:
        edges = edges_from_facts(store.subject_facts(TUID(str(subject)), as_of=as_of))
        found.extend(children(edges, parent, as_of=as_of, relationship_type=relationship_type))
    return tuple(sorted(set(found)))


__all__ = [
    "DIRECT_PARENT_TYPE",
    "ULTIMATE_PARENT_TYPE",
    "Ancestry",
    "EntityUnknownError",
    "ancestry_of",
    "children_of",
]
