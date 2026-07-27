"""I1 kill-test, store boundary: the storage layer rejects writes without provenance."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import TUID, new_tuid
from treble.core.provenance import ExtractionMethod, Provenance, ProvenanceId, trace
from treble.store.duck import DuckStore, MissingProvenanceError

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> DuckStore:
    return DuckStore(tmp_path / "test.db")


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


def make_fact(subject: TUID, pid: ProvenanceId) -> Fact:
    return Fact(
        subject=subject,
        field="PX_LAST",
        value=101.25,
        effective_from=date(2026, 7, 24),
        knowledge_from=NOW,
        provenance_id=pid,
    )


def test_fact_with_dangling_provenance_rejected(store: DuckStore) -> None:
    fact = make_fact(new_tuid(), ProvenanceId("f" * 64))
    with pytest.raises(MissingProvenanceError):
        store.write_facts([fact])


def test_fact_with_stored_provenance_accepted_and_round_trips(store: DuckStore) -> None:
    prov = make_provenance()
    store.write_provenance([prov])
    subject = new_tuid()
    fact = make_fact(subject, prov.id)
    store.write_facts([fact])
    [read] = store.read(subject, "PX_LAST", as_of=NOW)
    assert read == fact


def test_every_read_fact_joins_to_provenance(store: DuckStore) -> None:
    prov = make_provenance()
    store.write_provenance([prov])
    subject = new_tuid()
    store.write_facts([make_fact(subject, prov.id)])
    for fact in store.read(subject, "PX_LAST", as_of=NOW):
        assert store.provenance(fact.provenance_id) == prov


def test_sptr_traversal_through_store(store: DuckStore) -> None:
    leaf = make_provenance(uri="https://example.test/filing")
    derived = make_provenance(
        uri="treble://model/derived",
        method=ExtractionMethod.DERIVED,
        input_ids=(leaf.id,),
    )
    store.write_provenance([leaf, derived])
    tree = trace(derived.id, store.provenance)
    assert tree.record == derived
    assert tree.inputs[0].record == leaf


def test_provenance_write_is_idempotent(store: DuckStore) -> None:
    prov = make_provenance()
    store.write_provenance([prov])
    store.write_provenance([prov])  # same content address — no-op, no error
    assert store.provenance(prov.id) == prov
