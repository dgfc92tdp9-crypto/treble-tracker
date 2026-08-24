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


def test_the_not_null_column_is_the_second_line_of_defence(store: DuckStore) -> None:
    """`write_facts` refuses a dangling id; the column refuses a null one.

    I1 claims both, and only the first had a test — removing `NOT NULL`
    from the DDL passed the entire suite. The column matters because it
    is the only guard on a path that does not go through `write_facts`:
    a migration script, a repair query, a future writer. Verified here by
    a direct INSERT, which is exactly what such a path would do.
    """
    import duckdb

    with pytest.raises(duckdb.Error, match="NOT NULL"):
        store._conn.execute(
            """
            INSERT INTO facts VALUES
            ('cik:1','Revenue','num',1.0,NULL,NULL,NULL,NULL,
             DATE '2025-12-31',NULL,TIMESTAMPTZ '2026-01-01 00:00:00+00', NULL)
            """
        )


def test_there_is_no_foreign_key_and_that_is_recorded(store: DuckStore) -> None:
    """A *dangling* id — non-null, but naming no provenance record — is
    refused by `write_facts` and accepted by a direct INSERT.

    Stated as a test rather than left implicit because a reader may
    reasonably assume the database enforces referential integrity here.
    It does not: the original design called for
    `provenance_id ... REFERENCES provenance(id)` and the column was
    built without it. On the live store the application check has held —
    0 dangling and 0 null across 13,300,231 facts — but that is the
    *application* holding, not the schema.

    If this test ever fails because an FK was added, that is good news:
    delete it and say so in the I1 docstring.
    """
    store._conn.execute(
        """
        INSERT INTO facts VALUES
        ('cik:2','Revenue','num',1.0,NULL,NULL,NULL,NULL,
         DATE '2025-12-31',NULL,TIMESTAMPTZ '2026-01-01 00:00:00+00','nosuchprovenance')
        """
    )
    dangling = store._conn.execute(
        "SELECT count(*) FROM facts f WHERE NOT EXISTS "
        "(SELECT 1 FROM provenance p WHERE p.id = f.provenance_id)"
    ).fetchone()
    assert dangling is not None and dangling[0] == 1, (
        "an FK now exists — remove this test and update the I1 docstring"
    )
