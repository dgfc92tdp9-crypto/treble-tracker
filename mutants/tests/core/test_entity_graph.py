"""Entity graph resolution — over the real GLEIF RR fixture and synthetic
conflict cases (mirrors ``tests/core/test_master.py``'s structure).
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from treble.core.entity_graph import (
    DIRECT_PARENT_TYPE,
    RelationshipEdge,
    children,
    conflicting_parents,
    direct_parent,
    edges_from_facts,
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
                knowledge_from=FETCHED,
                provenance_id="a",
            ),
            RelationshipEdge(
                child=child,
                parent=TUID("lei:CCCCCCCCCCCCCCCCCCCC"),
                relationship_type=DIRECT_PARENT_TYPE,
                status="ACTIVE",
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
