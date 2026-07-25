"""I1 kill-tests, model level: provenance is part of the value's type.

The store-boundary half of I1 (dangling provenance_id rejected on write)
lives in tests/store/. Remove the required provenance_id field, or the
content-addressed id, and these fail.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import (
    ExtractionMethod,
    Provenance,
    ProvenanceId,
    ProvenanceTree,
    trace,
)

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


def make_provenance(uri: str = "https://example.test/doc", **overrides: object) -> Provenance:
    defaults: dict[str, object] = {
        "source_system": "test",
        "source_uri": uri,
        "retrieved_at": NOW,
        "method": ExtractionMethod.API,
        "extractor_version": "1",
    }
    defaults.update(overrides)
    return Provenance.model_validate(defaults)


class TestProvenanceRecord:
    def test_id_is_deterministic_content_hash(self) -> None:
        a, b = make_provenance(), make_provenance()
        assert a.id == b.id
        assert a.id != make_provenance(uri="https://example.test/other").id

    def test_id_invariant_under_timezone_representation(self) -> None:
        # The same instant expressed in a different zone must hash identically —
        # storage round-trips localise timestamps, and a representation-sensitive
        # content address would silently break replay (I5).
        from zoneinfo import ZoneInfo

        dublin = NOW.astimezone(ZoneInfo("Europe/Dublin"))
        assert make_provenance(retrieved_at=dublin).id == make_provenance().id

    def test_immutable(self) -> None:
        with pytest.raises(ValidationError):
            make_provenance().source_system = "changed"  # type: ignore[misc]

    def test_naive_retrieved_at_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_provenance(retrieved_at=datetime(2026, 7, 25, 12, 0))  # noqa: DTZ001

    def test_confidence_bounds(self) -> None:
        with pytest.raises(ValidationError):
            make_provenance(confidence=1.5)


class TestFactRequiresProvenance:
    def test_fact_without_provenance_cannot_be_constructed(self) -> None:
        with pytest.raises(ValidationError):
            Fact.model_validate(
                {
                    "subject": TUID("t1"),
                    "field": "PX_LAST",
                    "value": 101.25,
                    "effective_from": NOW.date(),
                    "knowledge_from": NOW,
                    # provenance_id deliberately absent
                }
            )


class TestTrace:
    """SPTR is one generic DAG traversal, not per-screen code."""

    def test_traverses_derived_dag(self) -> None:
        leaf_a = make_provenance(uri="https://example.test/a")
        leaf_b = make_provenance(uri="https://example.test/b")
        derived = make_provenance(
            uri="treble://model/yas",
            method=ExtractionMethod.DERIVED,
            input_ids=(leaf_a.id, leaf_b.id),
        )
        registry = {p.id: p for p in (leaf_a, leaf_b, derived)}
        tree = trace(derived.id, registry.__getitem__)
        assert tree.record == derived
        assert {t.record.source_uri for t in tree.inputs} == {
            "https://example.test/a",
            "https://example.test/b",
        }
        assert all(t.inputs == () for t in tree.inputs)

    def test_cycle_guard(self) -> None:
        # A malformed lookup that always returns a record referencing itself.
        record = make_provenance(input_ids=(ProvenanceId("self"),))

        def lookup(_pid: ProvenanceId) -> Provenance:
            return record

        with pytest.raises(ValueError, match="max_depth"):
            trace(ProvenanceId("self"), lookup)


def test_tree_model_is_frozen() -> None:
    tree = ProvenanceTree(record=make_provenance())
    with pytest.raises(ValidationError):
        tree.inputs = ()  # type: ignore[misc]
