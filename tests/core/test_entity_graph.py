"""Entity graph resolution — over the real GLEIF RR fixture and synthetic
conflict cases (mirrors ``tests/core/test_master.py``'s structure).
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from treble.core.entity_graph import (
    DIRECT_PARENT_TYPE,
    ULTIMATE_PARENT_TYPE,
    ParentOutcome,
    RelationshipEdge,
    children,
    conflicting_parents,
    direct_parent,
    edges_from_facts,
    parse_relationship_state,
    parse_relationship_state_field,
    relationship_state_field,
    resolve_parent,
    ultimate_parent,
)
from treble.core.identifiers import TUID
from treble.ingest.base import RawPayload
from treble.ingest.gleif import GleifRelationshipAdapter
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash

FIXTURE = Path(__file__).parent.parent / "fixtures" / "gleif" / "rr_sample.xml"
FETCHED = datetime(2026, 7, 27, 0, 20, tzinfo=UTC)


def fixture_edges(tmp_path: Path) -> list[RelationshipEdge]:
    adapter = GleifRelationshipAdapter(PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"))
    raw = RawPayload(data=FIXTURE.read_bytes(), source_uri="rr", fetched_at=FETCHED)
    batch = adapter.parse(raw, payload_hash(raw.data))
    return edges_from_facts(batch.facts)


class TestResolutionFromRealFixture:
    def test_direct_and_ultimate_parent_resolve(self, tmp_path: Path) -> None:
        edges = fixture_edges(tmp_path)
        child = TUID("lei:029200396H3K1YG7D555")
        parent = TUID("lei:2549003PEZXUT7MDBU41")
        assert direct_parent(edges, child, as_of=FETCHED) == parent
        assert ultimate_parent(edges, child, as_of=FETCHED) == parent

    def test_children_reverse_lookup(self, tmp_path: Path) -> None:
        edges = fixture_edges(tmp_path)
        parent = TUID("lei:2549003PEZXUT7MDBU41")
        assert TUID("lei:029200396H3K1YG7D555") in children(edges, parent, as_of=FETCHED)

    def test_null_status_edge_excluded_by_default(self, tmp_path: Path) -> None:
        # Spec §8.1.4 / working agreement: a status the source itself
        # marked NULL is not treated as an active parent claim.
        edges = fixture_edges(tmp_path)
        child = TUID("lei:097900CADC0000229535")
        assert direct_parent(edges, child, as_of=FETCHED) is None

    def test_null_status_edge_visible_with_active_only_false(self, tmp_path: Path) -> None:
        edges = fixture_edges(tmp_path)
        child = TUID("lei:097900CADC0000229535")
        parent = TUID("lei:IYKCAVNFR8QGF00HV840")
        assert direct_parent(edges, child, as_of=FETCHED, active_only=False) == parent

    def test_unrelated_lei_resolves_to_none_not_a_guess(self, tmp_path: Path) -> None:
        edges = fixture_edges(tmp_path)
        assert direct_parent(edges, TUID("lei:00000000000000000000"), as_of=FETCHED) is None


class TestEvidence:
    def test_every_edge_carries_provenance(self, tmp_path: Path) -> None:
        edges = fixture_edges(tmp_path)
        assert edges
        assert all(edge.provenance_id for edge in edges)

    def test_resolution_is_point_in_time(self, tmp_path: Path) -> None:
        """I2: a relationship learned today must not be visible yesterday."""
        edges = fixture_edges(tmp_path)
        child = TUID("lei:029200396H3K1YG7D555")
        parent = TUID("lei:2549003PEZXUT7MDBU41")
        assert direct_parent(edges, child, as_of=FETCHED) == parent
        assert direct_parent(edges, child, as_of=FETCHED - timedelta(days=1)) is None


class TestConflicts:
    def test_real_fixture_has_no_conflicts(self, tmp_path: Path) -> None:
        edges = fixture_edges(tmp_path)
        assert conflicting_parents(edges, relationship_type=DIRECT_PARENT_TYPE, as_of=FETCHED) == []

    def test_conflicting_claim_is_reported_not_resolved(self) -> None:
        child = TUID("lei:AAAAAAAAAAAAAAAAAAAA")
        edges = [
            RelationshipEdge(
                child=child,
                parent=TUID("lei:BBBBBBBBBBBBBBBBBBBB"),
                relationship_type=DIRECT_PARENT_TYPE,
                status="ACTIVE",
                registration="PUBLISHED",
                knowledge_from=FETCHED,
                provenance_id="a",
            ),
            RelationshipEdge(
                child=child,
                parent=TUID("lei:CCCCCCCCCCCCCCCCCCCC"),
                relationship_type=DIRECT_PARENT_TYPE,
                status="ACTIVE",
                registration="PUBLISHED",
                knowledge_from=FETCHED,
                provenance_id="b",
            ),
        ]
        # Both claimants are real evidence; neither is silently preferred.
        assert direct_parent(edges, child, as_of=FETCHED) is None
        found = conflicting_parents(edges, relationship_type=DIRECT_PARENT_TYPE, as_of=FETCHED)
        assert len(found) == 1
        resolved_child, parents = found[0]
        assert resolved_child == child
        assert len(parents) == 2


class TestStatusStaysWithItsOwnCounterparty:
    """The defect this encoding exists to prevent, in the shape it took.

    On the live store ``lei:969500L37U9ILPNTDL21`` held two
    IS_ULTIMATELY_CONSOLIDATED_BY records:

    ==================== ============== ====================
    counterparty         RelStatus      RegistrationStatus
    ==================== ============== ====================
    894500NGP61K2MQO3X40 NULL           ANNULLED
    969500WDCPJAW65OHW35 ACTIVE         LAPSED
    ==================== ============== ====================

    The counterparty and the status were separate facts under separate
    keys, so the store's visibility window chose each independently: the
    counterparty by ``value_text`` ascending (``894500…``) and the status
    by "a stated value outranks a null" (``ACTIVE``). RELS then named the
    *annulled* record's parent and called it active.

    These LEIs are the real ones, and the alphabetical order is the point
    — a resolver that ignores status entirely picks ``894500…`` here.
    """

    CHILD = TUID("lei:969500L37U9ILPNTDL21")
    ANNULLED = TUID("lei:894500NGP61K2MQO3X40")
    ACTIVE = TUID("lei:969500WDCPJAW65OHW35")

    def _edges(self, active_status: str = "ACTIVE") -> list[RelationshipEdge]:
        return [
            RelationshipEdge(
                child=self.CHILD,
                parent=parent,
                relationship_type=ULTIMATE_PARENT_TYPE,
                status=status,
                registration=registration,
                knowledge_from=FETCHED,
                provenance_id="one-bulk-file",
            )
            # Same provenance and knowledge time for both, as one bulk file
            # gives them -- the old join keyed on exactly that and so could
            # not tell these two records apart. The registrations are the
            # live record's own: ANNULLED beside LAPSED.
            for parent, status, registration in (
                (self.ANNULLED, "NULL", "ANNULLED"),
                (self.ACTIVE, active_status, "LAPSED"),
            )
        ]

    def test_the_active_record_is_chosen_not_the_first_by_lei(self) -> None:
        # Fails if status is ignored: ANNULLED sorts first.
        assert ultimate_parent(self._edges(), self.CHILD, as_of=FETCHED) == self.ACTIVE

    def test_resolution_says_it_resolved_and_on_what_evidence(self) -> None:
        found = resolve_parent(self._edges(), self.CHILD, ULTIMATE_PARENT_TYPE, as_of=FETCHED)
        assert found.outcome is ParentOutcome.RESOLVED
        assert found.parent == self.ACTIVE
        assert found.candidates == (self.ANNULLED, self.ACTIVE)

    def test_no_active_record_refuses_rather_than_naming_the_lapsed_one(self) -> None:
        # Both records superseded. The entity still has an ultimate parent;
        # GLEIF has simply stopped saying which, and so does this.
        found = resolve_parent(
            self._edges(active_status="INACTIVE"),
            self.CHILD,
            ULTIMATE_PARENT_TYPE,
            as_of=FETCHED,
        )
        assert found.outcome is ParentOutcome.NONE_ACTIVE
        assert found.parent is None
        # Refusing is not forgetting: both are still named as evidence.
        assert found.candidates == (self.ANNULLED, self.ACTIVE)

    def test_two_active_records_are_ambiguous_not_a_coin_toss(self) -> None:
        # Both live filings, so neither is disqualified and GLEIF is
        # genuinely naming two parents.
        edges = [
            e.model_copy(update={"status": "ACTIVE", "registration": "PUBLISHED"})
            for e in self._edges()
        ]
        found = resolve_parent(edges, self.CHILD, ULTIMATE_PARENT_TYPE, as_of=FETCHED)
        assert found.outcome is ParentOutcome.AMBIGUOUS
        assert found.parent is None

    def test_active_on_a_withdrawn_registration_is_not_a_live_parent(self) -> None:
        """The contradiction RegistrationStatus exists to catch.

        A record can only be believed if the filing carrying it is still
        published. ANNULLED means GLEIF withdrew the filing; a record
        claiming ACTIVE on one is asserting two incompatible things, and
        this store refuses it rather than picking the half it prefers.

        Never observed in the 663,410-record file — ANNULLED, RETIRED and
        DUPLICATE carry ACTIVE zero times — so this changes no answer
        today and exists to notice if that stops being true.
        """
        edges = [
            e.model_copy(update={"status": "ACTIVE"})
            for e in self._edges()
            if e.parent == self.ANNULLED  # registration=ANNULLED
        ]
        assert edges[0].withdrawn is True
        found = resolve_parent(edges, self.CHILD, ULTIMATE_PARENT_TYPE, as_of=FETCHED)
        assert found.parent is None
        assert found.outcome is ParentOutcome.NONE_ACTIVE

    def test_a_lapsed_registration_is_still_a_live_parent(self) -> None:
        # The counterweight, and the reason WITHDRAWN_REGISTRATIONS is a
        # named set rather than "anything but PUBLISHED": ACTIVE sits on a
        # LAPSED registration 99,532 times in the live file.
        edges = [e for e in self._edges() if e.parent == self.ACTIVE]
        assert edges[0].registration == "LAPSED"
        assert ultimate_parent(edges, self.CHILD, as_of=FETCHED) == self.ACTIVE

    def test_no_record_at_all_is_distinct_from_none_active(self) -> None:
        found = resolve_parent([], self.CHILD, ULTIMATE_PARENT_TYPE, as_of=FETCHED)
        assert found.outcome is ParentOutcome.NO_RECORD
        assert found.candidates == ()


class TestFieldEncoding:
    def test_round_trips_type_and_counterparty(self) -> None:
        field = relationship_state_field(ULTIMATE_PARENT_TYPE, "969500WDCPJAW65OHW35")
        assert field == "gleif:rr:IS_ULTIMATELY_CONSOLIDATED_BY:969500WDCPJAW65OHW35:state"
        assert parse_relationship_state_field(field) == (
            ULTIMATE_PARENT_TYPE,
            TUID("lei:969500WDCPJAW65OHW35"),
        )

    def test_the_hyphenated_gleif_spelling_survives_the_round_trip(self) -> None:
        # "IS_FUND-MANAGED_BY" is GLEIF's own spelling and is carried
        # verbatim; the hyphen must not disturb the split.
        field = relationship_state_field("IS_FUND-MANAGED_BY", "969500WDCPJAW65OHW35")
        assert parse_relationship_state_field(field) == (
            "IS_FUND-MANAGED_BY",
            TUID("lei:969500WDCPJAW65OHW35"),
        )

    def test_the_superseded_two_fact_encoding_is_not_parsed_as_a_counterparty(self) -> None:
        # Those facts are still in the store -- I2 deletes nothing -- and a
        # relationship *type* sits where the counterparty segment belongs.
        # Reading one as an edge would invent a parent named after a
        # relationship type.
        assert parse_relationship_state_field("gleif:rr:IS_SUBFUND_OF:status") is None
        assert parse_relationship_state_field("gleif:rr:IS_SUBFUND_OF") is None

    def test_the_v2_encoding_is_not_read_as_current(self) -> None:
        """The one a replay actually collides with.

        v2 keyed by counterparty exactly as now and differs only in the
        suffix, so nothing but the suffix separates them — and its values
        carry no registration, so a v2 fact read as current would be an
        edge that can never be `withdrawn` and would walk through the
        guard that reads it. Both encodings sit in the store together
        after a replay; only one is current.
        """
        v2 = "gleif:rr:IS_ULTIMATELY_CONSOLIDATED_BY:969500WDCPJAW65OHW35:status"
        assert parse_relationship_state_field(v2) is None

    def test_a_bare_status_value_is_read_as_a_status_not_a_registration(self) -> None:
        # `parse_relationship_state` stays total over the older shape, so a
        # value without a separator cannot come back as "no status".
        assert parse_relationship_state("ACTIVE") == ("ACTIVE", None)
        assert parse_relationship_state("ACTIVE/PUBLISHED") == ("ACTIVE", "PUBLISHED")
        assert parse_relationship_state(None) == (None, None)

    def test_unrelated_fields_are_not_relationship_records(self) -> None:
        assert parse_relationship_state_field("gleif:legalName") is None
        assert parse_relationship_state_field("nport:lei") is None
