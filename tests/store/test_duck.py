"""DuckStore write-path edge cases."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore, MissingProvenanceError


class TestAnEmptyBatchIsLegitimate:
    """A source with nothing to say for a day is normal, not an error.

    `write_facts([])` built `WHERE id IN ()` from an empty provenance set —
    a DuckDB syntax error. The guard that should have prevented it was
    written, and placed *below* the query it needed to protect, so it could
    never run in the case it existed for.

    Found by the DTCC tape, which publishes nothing on a weekend: asking
    for ten days took the whole refresh down with a parser error, which
    reads like a corrupted store rather than a quiet Saturday.
    """

    def test_no_facts_is_a_no_op(self, tmp_path: Path) -> None:
        store = DuckStore(tmp_path / "t.db")
        store.write_facts([])
        assert store.fact_count() == 0

    def test_no_provenance_is_a_no_op(self, tmp_path: Path) -> None:
        DuckStore(tmp_path / "p.db").write_provenance([])

    def test_an_empty_batch_does_not_disturb_what_is_stored(self, tmp_path: Path) -> None:
        """The dangerous version of this fix would be a bare early return
        that also skipped a needed flush. Writing, then writing nothing,
        must leave the first write intact and readable."""
        store = DuckStore(tmp_path / "m.db")
        record = Provenance(
            source_system="treasury-curve",
            source_uri="https://example.invalid/x",
            retrieved_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            method=ExtractionMethod.BULK_FILE,
            extractor_version="1",
            payload_hash="a" * 64,
        )
        store.write_provenance([record])
        store.write_facts(
            [
                Fact(
                    subject="govt:UST-CMT:10Y",
                    field="PAR_YIELD",
                    value=0.0465,
                    effective_from=date(2026, 7, 31),
                    effective_to=date(2026, 7, 31),
                    knowledge_from=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
                    provenance_id=record.id,
                )
            ]
        )
        before = store.fact_count()
        store.write_facts([])
        assert store.fact_count() == before
        assert store.read(
            TUID("govt:UST-CMT:10Y"),
            "PAR_YIELD",
            as_of=datetime(2026, 8, 8, tzinfo=UTC),
        )

    def test_a_dangling_provenance_id_is_still_caught(self, tmp_path: Path) -> None:
        """The early return must not have moved the referential check out
        of the path — a guard that fixed one bug by disabling another
        check would be a poor trade."""
        store = DuckStore(tmp_path / "d.db")
        with pytest.raises(MissingProvenanceError):
            store.write_facts(
                [
                    Fact(
                        subject="govt:UST-CMT:10Y",
                        field="PAR_YIELD",
                        value=0.0465,
                        effective_from=date(2026, 7, 31),
                        effective_to=date(2026, 7, 31),
                        knowledge_from=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
                        provenance_id="0" * 64,
                    )
                ]
            )
