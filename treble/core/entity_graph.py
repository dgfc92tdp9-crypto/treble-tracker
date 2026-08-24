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

**An entity has one ultimate parent; several records means one is current
and the rest are not.** GLEIF says which through ``RelationshipStatus``,
and that status is the only thing entitled to choose between them — so it
travels in the fact's *key*, beside the counterparty it belongs to
(:func:`relationship_state_field`), rather than as a separate fact that
has to be joined back on. Where no record is ACTIVE, resolution refuses
and says so (:class:`ParentOutcome`) instead of naming the lapsed one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict

from treble.core.facts import Fact
from treble.core.identifiers import TUID

#: The two relationship types that define the consolidation hierarchy.
DIRECT_PARENT_TYPE = "IS_DIRECTLY_CONSOLIDATED_BY"
ULTIMATE_PARENT_TYPE = "IS_ULTIMATELY_CONSOLIDATED_BY"

_RR_FIELD_PREFIX = "gleif:rr:"

#: Names what the value now holds: a record's whole state, both statuses
#: together, not a single status.
#:
#: **The suffix changes whenever the value's shape does, and that is the
#: mechanism, not a naming preference.** Nothing is deleted from this store
#: (I2), so a replay adds its facts beside the old ones. Where the field
#: name is reused, the two encodings land in the same partition and are
#: read as competing values of one key: replaying v3 under v2's `:status`
#: put `ACTIVE` and `ACTIVE/PUBLISHED` side by side, and the bare v2 value
#: — carrying no registration, so never `withdrawn` — would have walked
#: straight through the guard that reads it. A new suffix makes the old
#: facts inert instead of contradictory, which is how `:status` retired
#: v1's `gleif:rr:<TYPE>` in the first place.
_STATE_SUFFIX = ":state"

#: v2's suffix, and v1 wrote `gleif:rr:<TYPE>` with no suffix at all.
#: Recognised only so `parse_relationship_state_field` can say "not mine"
#: about facts that are still in the store and no longer read.
_SUPERSEDED_SUFFIX = ":status"

#: A relationship is usable for graph traversal only when GLEIF still
#: asserts it as current. INACTIVE/NULL-status edges are kept in the fact
#: store (nothing is dropped) but excluded from the default resolution.
ACTIVE_STATUS = "ACTIVE"

#: Separates the two halves of a relationship record's state in one value.
#: Neither GLEIF token contains it — they are enum words over ``A-Z_``.
_STATE_SEP = "/"

#: RegistrationStatus values that say the *filing* was withdrawn, as
#: against the relationship having ended.
#:
#: Counted over the 663,410-record file: ANNULLED (56,757), RETIRED
#: (125,088) and DUPLICATE (3) carry RelationshipStatus=ACTIVE **zero**
#: times. So the ACTIVE filter was already excluding every withdrawn
#: filing, and this set changes no answer today — it exists so that a
#: record claiming ACTIVE on a withdrawn registration is refused rather
#: than believed, which is a contradiction this store should not resolve
#: on its own.
#:
#: LAPSED is deliberately **not** here. It carries ACTIVE 99,532 times —
#: a lapsed *registration* is an LEI nobody renewed, not a relationship
#: anybody retracted — and treating it as withdrawn would silently drop a
#: sixth of the graph. PENDING_ARCHIVAL and PENDING_TRANSFER (89 ACTIVE
#: between them) are likewise in-flight administrative states, not
#: retractions.
WITHDRAWN_REGISTRATIONS = frozenset({"ANNULLED", "RETIRED", "DUPLICATE"})


def relationship_state_value(
    relationship_status: str | None, registration_status: str | None
) -> str:
    """One record's RelationshipStatus and RegistrationStatus, together.

    **They travel in a single value because they describe one filing, and
    the last thing separated from its own record cost 8,511 entities the
    right parent.** Given their own fields they would collide exactly as
    the counterparty and its status once did: an entity holding a live
    record and a withdrawn one puts two values under
    ``…:<CP>:status`` and two under ``…:<CP>:registration``, and nothing
    says which status belongs to which registration. There is no record
    identity in a subject/field/value store to join them back on, so the
    pairing has to be something the store cannot take apart.
    """
    return f"{relationship_status or ''}{_STATE_SEP}{registration_status or ''}"


def parse_relationship_state(value: object) -> tuple[str | None, str | None]:
    """``"ACTIVE/PUBLISHED"`` -> ``("ACTIVE", "PUBLISHED")``.

    Takes the fact's value as stored, which the store types as any of its
    scalar kinds — the caller should not have to narrow a field it already
    knows is text.

    A value with no separator is read as a bare RelationshipStatus with no
    registration — the shape parser v2 wrote, so a store replayed only as
    far as v2 still resolves parents correctly rather than reading every
    status as absent.
    """
    if value is None:
        return None, None
    status, sep, registration = str(value).partition(_STATE_SEP)
    if not sep:
        return (status or None), None
    return (status or None), (registration or None)


#: ISO 17442 shape only — the checksum is validated at ingest. Used here
#: to tell a counterparty segment from a relationship-type segment when
#: reading a field name back, so the old two-fact encoding below cannot be
#: mistaken for the current one.
_LEI_SHAPE = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")


def relationship_state_field(relationship_type: str, counterparty: str) -> str:
    """The field holding one relationship record's state.

    **The counterparty is part of the key, and that is the whole point.**

    The GLEIF adapter used to emit two facts per record —
    ``gleif:rr:<TYPE>`` holding the counterparty and
    ``gleif:rr:<TYPE>:status`` holding its status — which stated the two
    halves of one record as separate facts with nothing joining them.
    GLEIF lets an entity hold several records of the same type at once (a
    current one and a superseded one), so both keys held several values
    in a single partition, and the store's latest-knowledge-wins window
    picked one value for each *independently*.

    Measured on the live store, ``lei:969500L37U9ILPNTDL21``:

    ==================== ============== ====================
    counterparty         RelStatus      RegistrationStatus
    ==================== ============== ====================
    894500NGP61K2MQO3X40 NULL           ANNULLED
    969500WDCPJAW65OHW35 ACTIVE         LAPSED
    ==================== ============== ====================

    The window ranked the counterparty by ``value_text`` ascending and so
    chose ``894500…``; it ranked the status by "a stated value outranks a
    null" (``schema.TIE_BREAK``) and so chose ``ACTIVE``. Neither choice
    is wrong on its own, and the pair is an assertion GLEIF never made:
    the annulled record wearing the other record's status. No ordering
    fixes that, because the pairing was never stored.

    Putting the counterparty in the key makes each record's fields one
    partition, so a parent cannot be separated from its own status. It
    also makes the reverse lookup direct — the children of a parent under
    an active relationship are the subjects holding this field with a
    value starting ``ACTIVE``, one query rather than a join. (The value
    carries the RegistrationStatus behind it; see
    :func:`relationship_state_value`.)
    """
    return f"{_RR_FIELD_PREFIX}{relationship_type}:{counterparty.upper()}{_STATE_SUFFIX}"


def parse_relationship_state_field(field: str) -> tuple[str, TUID] | None:
    """``gleif:rr:<TYPE>:<COUNTERPARTY>:state`` -> (type, counterparty).

    ``None`` for anything else, including both superseded encodings, whose
    facts are still in the store because nothing is deleted from it (I2).

    v2's ``…:<COUNTERPARTY>:status`` is rejected on the suffix; v1's
    ``gleif:rr:<TYPE>:status`` would also fail the LEI shape below, since
    it leaves a relationship *type* where the counterparty segment
    belongs. Both checks are load-bearing: v2 shares v1's suffix and has a
    real LEI in the right place, so only the suffix separates it.
    """
    if not field.startswith(_RR_FIELD_PREFIX) or not field.endswith(_STATE_SUFFIX):
        return None
    body = field[len(_RR_FIELD_PREFIX) : -len(_STATE_SUFFIX)]
    relationship_type, _, counterparty = body.rpartition(":")
    if not relationship_type or not _LEI_SHAPE.match(counterparty):
        return None
    return relationship_type, TUID(f"lei:{counterparty}")


class RelationshipEdge(BaseModel):
    """One evidence-carrying relationship between two LEI-keyed entities."""

    model_config = ConfigDict(frozen=True)

    child: TUID
    parent: TUID
    relationship_type: str
    status: str | None
    #: RegistrationStatus of the filing that carried this relationship —
    #: whether GLEIF still publishes the *record*, as against whether the
    #: relationship is current. `None` for facts written before parser v3.
    registration: str | None
    knowledge_from: datetime
    provenance_id: str

    @property
    def withdrawn(self) -> bool:
        """Whether the filing itself was retracted.

        Distinct from a relationship that ended: an INACTIVE/RETIRED record
        says the ownership stopped, an ACTIVE/ANNULLED record would say the
        filing should never have existed while still claiming to be
        current. The second is a contradiction, and `_visible` declines to
        treat it as evidence of a live parent.
        """
        return self.registration in WITHDRAWN_REGISTRATIONS


def edges_from_facts(facts: Iterable[Fact]) -> list[RelationshipEdge]:
    """One edge per relationship-record fact.

    ``GleifRelationshipAdapter`` emits a single fact per RR-CDF record —
    ``gleif:rr:<TYPE>:<COUNTERPARTY>:status`` -> ACTIVE/INACTIVE/NULL —
    so the counterparty and its status arrive already joined and this is a
    decode rather than a join.

    It used to be a join, over ``(subject, type, provenance, knowledge
    time)``, and that key is identical for every record an entity holds of
    one type in one bulk file: the dict collapsed them and every edge came
    out wearing the last record's status. See
    :func:`relationship_state_field` for the live record that showed it.

    Facts in the superseded two-fact encoding are skipped. They remain in
    the store because nothing is ever deleted from it (I2), and a store
    that has not been re-parsed since therefore yields no edges — which
    surfaces as :class:`EntityUnknownError` at the caller, an honest
    "nothing known" rather than a confidently wrong parent.
    """
    edges: list[RelationshipEdge] = []
    for fact in facts:
        parsed = parse_relationship_state_field(fact.field)
        if parsed is None:
            continue
        relationship_type, counterparty = parsed
        status, registration = parse_relationship_state(fact.value)
        edges.append(
            RelationshipEdge(
                child=fact.subject,
                parent=counterparty,
                relationship_type=relationship_type,
                status=status,
                registration=registration,
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
        # A withdrawn filing is not evidence of a live parent even when it
        # calls itself ACTIVE. Never observed in the live file -- the three
        # withdrawn states carry ACTIVE zero times -- so this drops nothing
        # today and refuses a contradiction if the source ever files one.
        and (not active_only or (edge.status == ACTIVE_STATUS and not edge.withdrawn))
    ]


class ParentOutcome(Enum):
    """Why a parent lookup answered the way it did.

    An entity with no relationship record, one whose only records are
    superseded, and one GLEIF genuinely disagrees about are three
    different findings. All three previously returned ``None`` and read
    on screen as "no parent", which is a claim about the *world* rather
    than about this store's evidence.
    """

    RESOLVED = "resolved"
    #: GLEIF filed no record of this type for the entity.
    NO_RECORD = "no record"
    #: Records exist, none is ACTIVE. The entity has a parent; GLEIF is no
    #: longer asserting which. Naming the lapsed one would be a guess.
    NONE_ACTIVE = "no active record"
    #: More than one ACTIVE record of the same type. Reported, never
    #: resolved by preferring one (spec §8.1.4).
    AMBIGUOUS = "several active records"


@dataclass(frozen=True)
class ParentResolution:
    """The parent, and the evidence that produced it."""

    outcome: ParentOutcome
    parent: TUID | None
    #: Every counterparty considered, active or not — so a caller can say
    #: *which* records it refused to choose between.
    candidates: tuple[TUID, ...]

    def __bool__(self) -> bool:
        return self.parent is not None


def resolve_parent(
    edges: Iterable[RelationshipEdge],
    lei: TUID,
    relationship_type: str,
    *,
    as_of: datetime,
    active_only: bool = True,
) -> ParentResolution:
    """Resolve ``lei``'s parent of ``relationship_type`` as known at
    ``as_of`` (I2), with the reason attached — never a guess.

    The ACTIVE filter is the whole selection. An entity holding a current
    record and a superseded one has exactly one parent, and it is the one
    GLEIF still marks ACTIVE; picking by any other order (LEI, provenance,
    arrival) picks the annulled record about half the time.
    """
    considered = tuple(
        sorted(
            {
                edge.parent
                for edge in _visible(
                    edges,
                    relationship_type=relationship_type,
                    as_of=as_of,
                    active_only=False,
                )
                if edge.child == lei
            },
            key=str,
        )
    )
    if not considered:
        return ParentResolution(ParentOutcome.NO_RECORD, None, ())

    active = tuple(
        sorted(
            {
                edge.parent
                for edge in _visible(
                    edges,
                    relationship_type=relationship_type,
                    as_of=as_of,
                    active_only=active_only,
                )
                if edge.child == lei
            },
            key=str,
        )
    )
    if not active:
        return ParentResolution(ParentOutcome.NONE_ACTIVE, None, considered)
    if len(active) > 1:
        return ParentResolution(ParentOutcome.AMBIGUOUS, None, considered)
    return ParentResolution(ParentOutcome.RESOLVED, active[0], considered)


def direct_parent(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return resolve_parent(
        edges, lei, DIRECT_PARENT_TYPE, as_of=as_of, active_only=active_only
    ).parent


def ultimate_parent(
    edges: Iterable[RelationshipEdge], lei: TUID, *, as_of: datetime, active_only: bool = True
) -> TUID | None:
    return resolve_parent(
        edges, lei, ULTIMATE_PARENT_TYPE, as_of=as_of, active_only=active_only
    ).parent


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
