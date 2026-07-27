"""GleifRelationshipAdapter against a recorded fixture (no network).

``rr_sample.xml`` is eight real records trimmed from the live RR-CDF
concatenated file downloaded 2026-07-27 (660,674 records total) — one of
each relationship type observed in the file, plus one INACTIVE and one
NULL-status record, so status handling is exercised against real data
rather than synthesised cases.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from treble.core.identifiers import validate_lei
from treble.core.provenance import ExtractionMethod
from treble.ingest.base import RawPayload
from treble.ingest.gleif import (
    GleifRelationshipAdapter,
    UnsupportedRelationshipNodeError,
    relationship_field,
    relationship_status_field,
)
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash

FIXTURE = Path(__file__).parent.parent / "fixtures" / "gleif" / "rr_sample.xml"
FETCHED = datetime(2026, 7, 27, 0, 20, tzinfo=UTC)

RELATIONSHIP_TYPES = (
    "IS_DIRECTLY_CONSOLIDATED_BY",
    "IS_ULTIMATELY_CONSOLIDATED_BY",
    "IS_FUND-MANAGED_BY",
    "IS_SUBFUND_OF",
    "IS_INTERNATIONAL_BRANCH_OF",
    "IS_FEEDER_TO",
)


@pytest.fixture
def adapter(tmp_path: Path) -> GleifRelationshipAdapter:
    return GleifRelationshipAdapter(PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"))


def raw() -> RawPayload:
    return RawPayload(
        data=FIXTURE.read_bytes(),
        source_uri="https://leidata.gleif.org/api/v1/concatenated-files/rr/get/41753/zip",
        fetched_at=FETCHED,
    )


class TestParse:
    def test_parses_every_record_into_a_value_and_status_fact(
        self, adapter: GleifRelationshipAdapter
    ) -> None:
        batch = adapter.parse(raw(), payload_hash(raw().data))
        # 8 records * (1 value fact + 1 status fact).
        assert len(batch.facts) == 16

    def test_every_relationship_type_present_is_carried_through_verbatim(
        self, adapter: GleifRelationshipAdapter
    ) -> None:
        # No coined mnemonics: the field carries exactly what RR-CDF said,
        # including the file's own "IS_FUND-MANAGED_BY" spelling.
        batch = adapter.parse(raw(), payload_hash(raw().data))
        fields = {f.field for f in batch.facts}
        for rel_type in RELATIONSHIP_TYPES:
            assert relationship_field(rel_type) in fields
            assert relationship_status_field(rel_type) in fields

    def test_null_status_preserved_as_literal_not_dropped(
        self, adapter: GleifRelationshipAdapter
    ) -> None:
        # RR-CDF's RelationshipStatus=NULL is a real enum value ("status not
        # applicable"), distinct from missing data — must not collapse to
        # Python None or vanish.
        batch = adapter.parse(raw(), payload_hash(raw().data))
        status_facts = {
            f.value
            for f in batch.facts
            if f.field == relationship_status_field("IS_DIRECTLY_CONSOLIDATED_BY")
        }
        assert "NULL" in status_facts
        assert "ACTIVE" in status_facts

    def test_inactive_status_recorded_not_dropped(self, adapter: GleifRelationshipAdapter) -> None:
        batch = adapter.parse(raw(), payload_hash(raw().data))
        status_facts = {
            f.value
            for f in batch.facts
            if f.field == relationship_status_field("IS_FUND-MANAGED_BY")
        }
        assert status_facts == {"ACTIVE", "INACTIVE"}

    def test_every_lei_passes_our_own_checksum(self, adapter: GleifRelationshipAdapter) -> None:
        # Cross-validates the recorded live data against the independent
        # ISO 17442 mod-97 implementation, same convention as the OpenFIGI test.
        batch = adapter.parse(raw(), payload_hash(raw().data))
        for fact in batch.facts:
            assert str(fact.subject).startswith("lei:")
            validate_lei(str(fact.subject).removeprefix("lei:"))
        value_leis = {
            str(f.value)
            for f in batch.facts
            if f.field in {relationship_field(t) for t in RELATIONSHIP_TYPES}
        }
        for lei in value_leis:
            validate_lei(lei)

    def test_provenance_is_bulk_file(self, adapter: GleifRelationshipAdapter) -> None:
        batch = adapter.parse(raw(), payload_hash(raw().data))
        assert len(batch.provenance) == 1
        assert batch.provenance[0].method is ExtractionMethod.BULK_FILE
        assert all(f.provenance_id == batch.provenance[0].id for f in batch.facts)

    def test_parse_is_pure(self, adapter: GleifRelationshipAdapter) -> None:
        payload = raw()
        key = payload_hash(payload.data)
        assert adapter.parse(payload, key) == adapter.parse(payload, key)

    def test_rejects_non_rr_document(self, adapter: GleifRelationshipAdapter) -> None:
        bad = RawPayload(data=b"<not-rr/>", source_uri="x", fetched_at=FETCHED)
        with pytest.raises(ValueError, match="not an RR-CDF document"):
            adapter.parse(bad, payload_hash(bad.data))

    def test_rejects_non_lei_node_id_type(self, adapter: GleifRelationshipAdapter) -> None:
        doc = b"""<?xml version="1.0" encoding="UTF-8"?>
<rr:RelationshipData xmlns:rr="http://www.gleif.org/data/schema/rr/2016">
<rr:Header/>
<rr:RelationshipRecords>
<rr:RelationshipRecord>
  <rr:Relationship>
    <rr:StartNode><rr:NodeID>X</rr:NodeID><rr:NodeIDType>ISO17442_ALT</rr:NodeIDType></rr:StartNode>
    <rr:EndNode><rr:NodeID>Y</rr:NodeID><rr:NodeIDType>LEI</rr:NodeIDType></rr:EndNode>
    <rr:RelationshipType>IS_DIRECTLY_CONSOLIDATED_BY</rr:RelationshipType>
    <rr:RelationshipStatus>ACTIVE</rr:RelationshipStatus>
  </rr:Relationship>
</rr:RelationshipRecord>
</rr:RelationshipRecords>
</rr:RelationshipData>"""
        bad = RawPayload(data=doc, source_uri="x", fetched_at=FETCHED)
        with pytest.raises(UnsupportedRelationshipNodeError):
            adapter.parse(bad, payload_hash(bad.data))
