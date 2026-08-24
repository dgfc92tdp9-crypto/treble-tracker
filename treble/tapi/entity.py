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
    ACTIVE_STATUS,
    DIRECT_PARENT_TYPE,
    ULTIMATE_PARENT_TYPE,
    ParentOutcome,
    ParentResolution,
    RelationshipEdge,
    edges_from_facts,
    relationship_status_field,
    resolve_parent,
)
from treble.core.identifiers import TUID
from treble.store.duck import DuckStore


class EntityUnknownError(RuntimeError):
    """No relationship evidence for this entity, with what was looked for."""


@dataclass(frozen=True)
class Ancestry:
    """Who owns an entity, as GLEIF asserts it."""

    subject: str
    #: The full resolutions, carrying *why* a parent is absent. A screen
    #: that only has `None` cannot tell "GLEIF filed no record" from
    #: "every record GLEIF filed has lapsed", and those need different
    #: words: the first is silence, the second is a parent this store
    #: declines to name.
    direct: ParentResolution
    ultimate: ParentResolution
    #: Every edge the entity itself asserts, so `SPTR` can show the chain
    #: rather than only its endpoints.
    edges: tuple[RelationshipEdge, ...]

    @property
    def direct_parent(self) -> TUID | None:
        return self.direct.parent

    @property
    def ultimate_parent(self) -> TUID | None:
        return self.ultimate.parent

    @property
    def parents_agree(self) -> bool:
        """Whether the direct and ultimate parents are the same entity.

        They often are, and where they are not that is information rather
        than a fault: GLEIF's ultimate-parent assertion may skip an
        intermediate holding company the direct edge names.

        Two unresolved lookups are not agreement — an entity whose records
        have all lapsed at both levels would otherwise report its parents
        as agreeing, on no evidence at all.
        """
        if self.direct.parent is None or self.ultimate.parent is None:
            return False
        return self.direct.parent == self.ultimate.parent


def ancestry_of(store: DuckStore, subject: TUID, *, as_of: datetime) -> Ancestry:
    """Direct and ultimate parent for one entity, point-in-time.

    Both are read from the entity's own asserted edges. Neither is derived
    from the other — see the module docstring on why walking the direct
    chain to infer an ultimate parent would answer a different question
    from the one GLEIF answers.
    """
    # Every record, not one per key. GLEIF sometimes files a live record
    # and a withdrawn one against the same counterparty on the same day —
    # on the live store, three, each an ACTIVE/PUBLISHED beside a
    # NULL/ANNULLED or NULL/PENDING_ARCHIVAL. Those share a partition, so
    # the ordinary window returns whichever `TIE_BREAK` ranks first, and
    # for two rows differing only in value that is `value_text` ascending:
    # the right record was surfacing because `'ACTIVE'` happens to precede
    # `'NULL'` in the alphabet. Weighing them is this module's job, so it
    # asks for all of them and lets `resolve_parent` select on status.
    facts = store.subject_facts(subject, as_of=as_of, include_ties=True)
    edges = edges_from_facts(facts)
    if not edges:
        raise EntityUnknownError(
            f"{subject}: no GLEIF relationship facts as of {as_of.date()}. An entity with "
            "no parent and an entity nobody has filed a relationship for are different, "
            "and this is the second"
        )
    return Ancestry(
        subject=str(subject),
        direct=resolve_parent(edges, subject, DIRECT_PARENT_TYPE, as_of=as_of),
        ultimate=resolve_parent(edges, subject, ULTIMATE_PARENT_TYPE, as_of=as_of),
        edges=tuple(edges),
    )


def children_of(
    store: DuckStore,
    parent: TUID,
    *,
    as_of: datetime,
    relationship_type: str = DIRECT_PARENT_TYPE,
) -> tuple[TUID, ...]:
    """Entities asserting `parent` as their parent of this type, actively.

    Answered by one reverse query rather than by walking subjects. The
    first version scanned every LEI subject and documented it as "this
    scans"; the second refused above a threshold, having measured that the
    live store's 373,125 subjects would make a screen appear to hang.

    Both were solving the wrong problem. The rows sit in one table and the
    store can answer this directly — the expense was a Python loop, not a
    missing index. `DuckStore.subjects_with_value` is that query, and the
    threshold is gone because there is nothing left to guard against.

    A child asserts its parent on its *own* subject, so the subjects this
    returns are the children and the value matched is the status.

    **The status is the value, so the ACTIVE filter is the query rather
    than a pass afterwards.** This used to match `gleif:rr:<TYPE>` against
    the parent's LEI, which returned every child that had *ever* asserted
    this parent — a family swollen with lapsed members, none of them
    distinguishable from the current ones. With the counterparty in the
    key, asking for the value `ACTIVE` under that key is the same single
    indexed lookup and returns only the live relationships.
    """
    lei = str(parent).removeprefix("lei:")
    return tuple(
        store.subjects_with_value(
            relationship_status_field(relationship_type, lei), ACTIVE_STATUS, as_of=as_of
        )
    )


__all__ = [
    "DIRECT_PARENT_TYPE",
    "ULTIMATE_PARENT_TYPE",
    "Ancestry",
    "EntityUnknownError",
    "ParentOutcome",
    "ParentResolution",
    "ancestry_of",
    "children_of",
]
