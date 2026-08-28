"""Comparing two stores — including that a difference is actually reported.

`scripts/check_replay.py` is only worth its nightly minute if this module
says "different" when the stores are. Every test below that asserts equality
has a partner that changes one thing and asserts the opposite.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.compare import CONTENT_COLUMNS, Digest, StoreComparison
from treble.store.duck import DuckStore
from treble.store.schema import FACT_COLUMNS

KNOWN = datetime(2026, 8, 1, tzinfo=UTC)


def _store(path: Path, *, system: str = "src", value: float = 1.0, n: int = 3) -> None:
    """A closed store holding `n` facts, so it can be attached."""
    store = DuckStore(path)
    record = Provenance(
        source_system=system,
        source_uri="https://example.invalid/x",
        retrieved_at=KNOWN,
        method=ExtractionMethod.BULK_FILE,
        extractor_version="1",
        payload_hash="a" * 64,
    )
    store.write_provenance([record])
    store.write_facts(
        [
            Fact(
                subject=TUID(f"lei:{i}"),
                field="f",
                value=value,
                effective_from=date(2026, 1, 1),
                effective_to=None,
                knowledge_from=KNOWN,
                provenance_id=record.id,
            )
            for i in range(n)
        ]
    )
    store.close()


class TestIdentical:
    def test_two_identical_stores_compare_exact(self, tmp_path: Path) -> None:
        _store(tmp_path / "a.db")
        _store(tmp_path / "b.db")
        with StoreComparison(tmp_path / "a.db", tmp_path / "b.db") as c:
            (result,) = c.compare_all()
        assert result.exact and result.verdict == "identical"
        assert (result.only_left, result.only_right) == (0, 0)


class TestDifferencesAreReported:
    """The half that makes the gate worth having."""

    def test_a_changed_value_is_not_exact(self, tmp_path: Path) -> None:
        _store(tmp_path / "a.db", value=1.0)
        _store(tmp_path / "b.db", value=2.0)
        with StoreComparison(tmp_path / "a.db", tmp_path / "b.db") as c:
            (result,) = c.compare_all()
        assert not result.exact
        assert (result.only_left, result.only_right) == (3, 3)
        assert result.verdict == "diverged both ways"

    def test_a_missing_fact_is_a_subset(self, tmp_path: Path) -> None:
        _store(tmp_path / "a.db", n=3)
        _store(tmp_path / "b.db", n=2)
        with StoreComparison(tmp_path / "a.db", tmp_path / "b.db") as c:
            (result,) = c.compare_all()
        assert result.verdict == "subset"
        assert (result.only_left, result.only_right) == (1, 0)

    def test_an_extra_fact_is_a_superset(self, tmp_path: Path) -> None:
        _store(tmp_path / "a.db", n=2)
        _store(tmp_path / "b.db", n=3)
        with StoreComparison(tmp_path / "a.db", tmp_path / "b.db") as c:
            (result,) = c.compare_all()
        assert result.verdict == "superset"

    def test_a_source_absent_from_one_side_is_reported(self, tmp_path: Path) -> None:
        """Not silently skipped: a replay that produced nothing for a
        source would otherwise compare clean against a store that has it."""
        _store(tmp_path / "a.db", system="present")
        _store(tmp_path / "b.db", system="other")
        with StoreComparison(tmp_path / "a.db", tmp_path / "b.db") as c:
            results = {r.source: r for r in c.compare_all()}
        assert results["present"].verdict == "not reproduced"
        assert results["present"].right == 0


class TestProvenanceRenames:
    def test_a_renamed_source_differs_exactly_but_not_in_content(self, tmp_path: Path) -> None:
        """The case that made two comparisons necessary. Provenance ids are
        derived from their fields, so renaming `source_system` changes the
        id on every fact while changing nothing about the facts — 8.07
        million of them on the live store."""
        _store(tmp_path / "a.db", system="old-name")
        _store(tmp_path / "b.db", system="new-name")
        with StoreComparison(tmp_path / "a.db", tmp_path / "b.db") as c:
            result = c.compare("renamed", ("old-name",), ("new-name",))
        assert not result.exact, "provenance_id must differ after a rename"
        assert result.same_content, "and nothing else may"
        assert result.verdict == "identical content, provenance differs"

    def test_an_unaliased_rename_looks_like_two_missing_sources(self, tmp_path: Path) -> None:
        """Why aliases are stated rather than guessed. Without one, the
        same data reads as one source gone and another appeared — which is
        also exactly what a genuinely lost source looks like, so the
        comparison must not infer the difference from a matching count."""
        _store(tmp_path / "a.db", system="old-name")
        _store(tmp_path / "b.db", system="new-name")
        with StoreComparison(tmp_path / "a.db", tmp_path / "b.db") as c:
            verdicts = {r.source: r.verdict for r in c.compare_all()}
        assert verdicts == {"old-name": "not reproduced", "new-name": "superset"}


class TestHashing:
    def test_the_checksum_is_order_independent(self, tmp_path: Path) -> None:
        """Two stores holding the same facts written in different orders
        are the same store. Row order is a storage detail."""
        a, b = tmp_path / "a.db", tmp_path / "b.db"
        for path, order in ((a, range(3)), (b, reversed(range(3)))):
            store = DuckStore(path)
            record = Provenance(
                source_system="src",
                source_uri="https://example.invalid/x",
                retrieved_at=KNOWN,
                method=ExtractionMethod.BULK_FILE,
                extractor_version="1",
                payload_hash="a" * 64,
            )
            store.write_provenance([record])
            store.write_facts(
                [
                    Fact(
                        subject=TUID(f"lei:{i}"),
                        field="f",
                        value=1.0,
                        effective_from=date(2026, 1, 1),
                        effective_to=None,
                        knowledge_from=KNOWN,
                        provenance_id=record.id,
                    )
                    for i in order
                ]
            )
            store.close()
        with StoreComparison(a, b) as c:
            assert c.compare_all()[0].exact

    def test_a_duplicated_fact_changes_the_checksum(self, tmp_path: Path) -> None:
        """`sum` not `bit_xor`: XOR cancels in pairs, so a store holding
        every fact twice would hash identically to one holding each once."""
        _store(tmp_path / "a.db", n=2)
        _store(tmp_path / "b.db", n=2)
        dup = DuckStore(tmp_path / "b.db")
        rows = dup._conn.execute("SELECT * FROM facts").fetchall()
        dup._conn.executemany(
            f"INSERT INTO facts VALUES ({', '.join('?' * len(FACT_COLUMNS))})",  # noqa: S608
            rows,
        )
        dup.close()
        with StoreComparison(tmp_path / "a.db", tmp_path / "b.db") as c:
            assert not c.compare_all()[0].exact


def test_content_columns_exclude_only_provenance() -> None:
    assert set(CONTENT_COLUMNS) == set(FACT_COLUMNS) - {"provenance_id"}


def test_an_empty_digest_is_falsy() -> None:
    assert not Digest(rows=0, checksum=0)
    assert Digest(rows=1, checksum=0)
