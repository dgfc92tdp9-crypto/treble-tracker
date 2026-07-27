"""``fact_count`` — what lets an empty store announce itself.

Every bound cell in an unpopulated store renders as a dash, and a dash is
indistinguishable from "the company did not report this". The clients
check the count at startup so the two cases cannot be confused.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from treble.core.facts import Fact
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore


def test_empty_store_reports_zero(tmp_path: Path) -> None:
    assert DuckStore(tmp_path / "empty.db").fact_count() == 0


def test_count_reflects_what_was_written(tmp_path: Path) -> None:
    store = DuckStore(tmp_path / "t.db")
    provenance = Provenance(
        source_system="test",
        source_uri="https://example.test/filing/1",
        retrieved_at=datetime(2026, 7, 27, tzinfo=UTC),
        method=ExtractionMethod.XBRL,
        extractor_version="1",
    )
    store.write_provenance([provenance])
    store.write_facts(
        [
            Fact(
                subject="TEST",
                field="PX_LAST",
                value=1.0,
                effective_from=datetime(2026, 7, 1, tzinfo=UTC),
                knowledge_from=datetime(2026, 7, 1, tzinfo=UTC),
                provenance_id=provenance.id,
            )
        ]
    )
    assert DuckStore(tmp_path / "t.db").fact_count() == 1
