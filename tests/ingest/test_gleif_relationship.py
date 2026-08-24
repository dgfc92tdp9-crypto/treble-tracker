"""GleifRelationshipAdapter against a recorded fixture (no network).

``rr_sample.xml`` is eight real records trimmed from the live RR-CDF
concatenated file downloaded 2026-07-27 (660,674 records total) — one of
each relationship type observed in the file, plus one INACTIVE and one
NULL-status record, so status handling is exercised against real data
rather than synthesised cases.
"""

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from treble.core.entity_graph import (
    parse_relationship_status_field,
)
from treble.core.identifiers import validate_lei
from treble.core.provenance import ExtractionMethod
from treble.ingest.base import RawPayload
from treble.ingest.gleif import (
    GleifRelationshipAdapter,
    UnsupportedRelationshipNodeError,
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


def _statuses_for(batch, relationship_type: str) -> set[str | None]:
    """Every status filed for one relationship type in the batch."""
    found = set()
    for fact in batch.facts:
        parsed = parse_relationship_status_field(fact.field)
        assert parsed is not None
        if parsed[0] == relationship_type:
            found.add(fact.value)
    return found


def raw() -> RawPayload:
    return RawPayload(
        data=FIXTURE.read_bytes(),
        source_uri="https://leidata.gleif.org/api/v1/concatenated-files/rr/get/41753/zip",
        fetched_at=FETCHED,
    )


class TestParse:
    def test_parses_every_record_into_exactly_one_fact(
        self, adapter: GleifRelationshipAdapter
    ) -> None:
        # One fact per record, not two. The counterparty is in the key and
        # the status is the value, so there is nothing left to pair up --
        # and nothing that can be paired up wrongly.
        batch = adapter.parse(raw(), payload_hash(raw().data))
        assert len(batch.facts) == 8

    def test_each_fact_key_carries_its_own_counterparty(
        self, adapter: GleifRelationshipAdapter
    ) -> None:
        batch = adapter.parse(raw(), payload_hash(raw().data))
        for fact in batch.facts:
            parsed = parse_relationship_status_field(fact.field)
            assert parsed is not None, fact.field
            _, counterparty = parsed
            validate_lei(str(counterparty).removeprefix("lei:"))

    def test_every_relationship_type_present_is_carried_through_verbatim(
        self, adapter: GleifRelationshipAdapter
    ) -> None:
        # No coined mnemonics: the field carries exactly what RR-CDF said,
        # including the file's own "IS_FUND-MANAGED_BY" spelling.
        batch = adapter.parse(raw(), payload_hash(raw().data))
        types = set()
        for fact in batch.facts:
            parsed = parse_relationship_status_field(fact.field)
            assert parsed is not None
            types.add(parsed[0])
        assert types == set(RELATIONSHIP_TYPES)

    def test_null_status_preserved_as_literal_not_dropped(
        self, adapter: GleifRelationshipAdapter
    ) -> None:
        # RR-CDF's RelationshipStatus=NULL is a real enum value ("status not
        # applicable"), distinct from missing data — must not collapse to
        # Python None or vanish.
        batch = adapter.parse(raw(), payload_hash(raw().data))
        assert {"NULL", "ACTIVE"} <= _statuses_for(batch, "IS_DIRECTLY_CONSOLIDATED_BY")

    def test_inactive_status_recorded_not_dropped(self, adapter: GleifRelationshipAdapter) -> None:
        batch = adapter.parse(raw(), payload_hash(raw().data))
        assert _statuses_for(batch, "IS_FUND-MANAGED_BY") == {"ACTIVE", "INACTIVE"}

    def test_each_status_stays_with_the_counterparty_it_was_filed_against(
        self, adapter: GleifRelationshipAdapter
    ) -> None:
        # The defect this encoding exists to prevent: two records of one
        # type for one entity, and a status that could be read against the
        # wrong counterparty. Asserted against the real fixture's own
        # IS_DIRECTLY_CONSOLIDATED_BY records, which carry both an ACTIVE
        # and a NULL.
        batch = adapter.parse(raw(), payload_hash(raw().data))
        pairs = {}
        for fact in batch.facts:
            parsed = parse_relationship_status_field(fact.field)
            assert parsed is not None
            rel_type, counterparty = parsed
            if rel_type == "IS_DIRECTLY_CONSOLIDATED_BY":
                pairs[str(counterparty)] = fact.value
        assert pairs == {
            "lei:2549003PEZXUT7MDBU41": "ACTIVE",
            "lei:IYKCAVNFR8QGF00HV840": "NULL",
        }

    def test_every_lei_passes_our_own_checksum(self, adapter: GleifRelationshipAdapter) -> None:
        # Cross-validates the recorded live data against the independent
        # ISO 17442 mod-97 implementation, same convention as the OpenFIGI test.
        batch = adapter.parse(raw(), payload_hash(raw().data))
        for fact in batch.facts:
            assert str(fact.subject).startswith("lei:")
            validate_lei(str(fact.subject).removeprefix("lei:"))
            parsed = parse_relationship_status_field(fact.field)
            assert parsed is not None
            validate_lei(str(parsed[1]).removeprefix("lei:"))

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


class TestMalformedRelationshipPeriods:
    """Found by running the adapter against the live concatenated file
    (663,410 records) rather than the fixtures, which carry no such record.
    Every test passed while a full ingest could not complete.
    """

    @staticmethod
    def _rr(start: str | None, end: str | None) -> bytes:
        period = "".join(
            (
                "<rr:RelationshipPeriod><rr:PeriodType>RELATIONSHIP_PERIOD</rr:PeriodType>",
                f"<rr:StartDate>{start}</rr:StartDate>" if start else "",
                f"<rr:EndDate>{end}</rr:EndDate>" if end else "",
                "</rr:RelationshipPeriod>",
            )
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<rr:RelationshipData xmlns:rr="http://www.gleif.org/data/schema/rr/2016">'
            "<rr:RelationshipRecords><rr:RelationshipRecord><rr:Relationship>"
            "<rr:StartNode><rr:NodeID>529900T8BM49AURSDO55</rr:NodeID>"
            "<rr:NodeIDType>LEI</rr:NodeIDType></rr:StartNode>"
            "<rr:EndNode><rr:NodeID>254900HROIFWPRGM1V77</rr:NodeID>"
            "<rr:NodeIDType>LEI</rr:NodeIDType></rr:EndNode>"
            "<rr:RelationshipType>IS_DIRECTLY_CONSOLIDATED_BY</rr:RelationshipType>"
            "<rr:RelationshipStatus>ACTIVE</rr:RelationshipStatus>"
            f"<rr:RelationshipPeriods>{period}</rr:RelationshipPeriods>"
            "</rr:Relationship></rr:RelationshipRecord></rr:RelationshipRecords>"
            "</rr:RelationshipData>"
        ).encode()

    def _parse(self, body: bytes) -> int:
        adapter = GleifRelationshipAdapter(
            PayloadStore(Path(tempfile.mkdtemp())),
            IngestLog(Path(tempfile.mkdtemp()) / "l.db"),
        )
        payload = RawPayload(
            data=body,
            source_uri="https://leidata.gleif.org/x",
            fetched_at=datetime(2026, 8, 8, 9, 0, tzinfo=UTC),
        )
        return len(adapter.parse(payload, payload_hash(body)).facts)

    def test_an_end_before_its_start_is_skipped_not_repaired(self) -> None:
        """A filer has reported a relationship that ended before it began.
        Swapping the dates would invent a lifetime GLEIF never asserted, so
        the record is dropped."""
        assert self._parse(self._rr("2024-01-01", "2020-01-01")) == 0

    def test_an_end_with_no_start_becomes_a_single_day(self) -> None:
        """GLEIF records a lapsed relationship with EndDate filled and
        StartDate empty. Falling back to the fetch date asserts it began
        today and ended in the past, which is not a period at all."""
        assert self._parse(self._rr(None, "2020-01-01")) > 0

    def test_an_ordinary_period_is_unaffected(self) -> None:
        assert self._parse(self._rr("2020-01-01", "2024-01-01")) > 0
