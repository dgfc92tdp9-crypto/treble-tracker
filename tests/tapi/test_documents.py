"""The `docs` service (spec §8.3).

Three properties that would fail silently: a document served from a
re-fetch rather than from the stored bytes, a redistribution-restricted
payload served without its flag, and a subject's document list answering
today's question when asked about a past date.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore
from treble.tapi.documents import (
    DocumentUnavailableError,
    document_bytes,
    documents_for,
    ingest_history,
)

EARLY = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
LATE = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
SUBJECT = TUID("equity:IBM")


@pytest.fixture
def store(tmp_path: Path) -> DuckStore:
    return DuckStore(tmp_path / "t.db")


@pytest.fixture
def payloads(tmp_path: Path) -> PayloadStore:
    return PayloadStore(tmp_path / "payloads")


def _write(
    store: DuckStore,
    payloads: PayloadStore,
    *,
    body: bytes,
    source: str,
    when: datetime,
    fields: tuple[str, ...],
) -> str:
    key = payloads.put(body)
    prov = Provenance(
        source_system=source,
        source_uri=f"https://example.invalid/{source}",
        retrieved_at=when,
        method=ExtractionMethod.API,
        extractor_version="1",
        payload_hash=str(key),
    )
    store.write_provenance([prov])
    store.write_facts(
        [
            Fact(
                subject=str(SUBJECT),
                field=field,
                value=1.0,
                effective_from=date(2026, 6, 30),
                effective_to=date(2026, 6, 30),
                knowledge_from=when,
                provenance_id=prov.id,
            )
            for field in fields
        ]
    )
    return str(key)


class TestTheDocumentIsTheStoredOne:
    def test_bytes_come_back_exactly_as_stored(
        self, store: DuckStore, payloads: PayloadStore
    ) -> None:
        """Content-addressed, so this cannot serve a revised version of the
        document the facts were parsed from. EDGAR restates and vendors
        correct; a hyperlink resolves to today's version."""
        body = b'{"values": [{"close": "233.43"}]}'
        key = _write(
            store, payloads, body=body, source="twelvedata", when=LATE, fields=("ADJ_CLOSE",)
        )
        assert document_bytes(payloads, key) == body

    def test_a_missing_payload_names_the_broken_replay_chain(self, payloads: PayloadStore) -> None:
        """Not a missing file: the facts derived from it can no longer be
        reproduced, which is an I5 failure and should read as one."""
        with pytest.raises(DocumentUnavailableError, match="replay chain"):
            document_bytes(payloads, "0" * 64)


class TestTheListing:
    def test_documents_come_back_newest_first_with_their_fact_counts(
        self, store: DuckStore, payloads: PayloadStore
    ) -> None:
        """A payload that produced one field and one that produced four are
        different things to a reader deciding what to open."""
        _write(store, payloads, body=b"old", source="edgar", when=EARLY, fields=("A",))
        _write(
            store,
            payloads,
            body=b"new",
            source="twelvedata",
            when=LATE,
            fields=("B", "C", "D"),
        )
        refs = documents_for(store, SUBJECT, as_of=datetime.now(UTC))
        assert [r.source_system for r in refs] == ["twelvedata", "edgar"]
        assert [r.fact_count for r in refs] == [3, 1]

    def test_the_listing_is_point_in_time(self, store: DuckStore, payloads: PayloadStore) -> None:
        """I2. Asking what documents a subject had in July must give July's
        answer, or the drill-down disagrees with the screen it opened from."""
        _write(store, payloads, body=b"old", source="edgar", when=EARLY, fields=("A",))
        _write(store, payloads, body=b"new", source="twelvedata", when=LATE, fields=("B",))
        july = documents_for(store, SUBJECT, as_of=datetime(2026, 7, 15, tzinfo=UTC))
        assert [r.source_system for r in july] == ["edgar"]

    def test_a_subject_with_no_facts_is_an_error_not_an_empty_list(self, store: DuckStore) -> None:
        """An unknown subject and a subject with no sources render the same
        and are not the same."""
        with pytest.raises(DocumentUnavailableError, match="no facts"):
            documents_for(store, TUID("equity:NOPE"), as_of=datetime.now(UTC))


class TestRedistributionTravels:
    def test_a_restricted_source_is_flagged_on_the_document(
        self, store: DuckStore, payloads: PayloadStore
    ) -> None:
        """The payload is the most concentrated form of the source's data --
        the whole document rather than the fields parsed out of it. Serving
        facts under a restriction while serving the document freely would
        make the guard decorative."""
        _write(store, payloads, body=b"x", source="twelvedata", when=LATE, fields=("A",))
        assert documents_for(store, SUBJECT, as_of=datetime.now(UTC))[0].redistribution_restricted

    def test_an_unrestricted_source_is_not_flagged(
        self, store: DuckStore, payloads: PayloadStore
    ) -> None:
        _write(store, payloads, body=b"x", source="edgar", when=LATE, fields=("A",))
        refs = documents_for(store, SUBJECT, as_of=datetime.now(UTC))
        assert refs[0].redistribution_restricted is False


class TestIngestHistory:
    def test_it_reads_the_log_rather_than_the_facts(self, tmp_path: Path) -> None:
        """ "This adapter ingested nothing" and "this adapter ingested a
        payload that produced no facts" render the same on a screen built
        from facts alone, and only the second is a parser problem."""
        log = IngestLog(tmp_path / "l.db")
        log.append(
            source="twelvedata",
            payload_hash="a" * 64,
            source_uri="https://example.invalid/x",
            fetched_at=LATE,
            parser_version="1",
        )
        log.append(
            source="edgar",
            payload_hash="b" * 64,
            source_uri="https://example.invalid/y",
            fetched_at=LATE,
            parser_version="2",
        )
        assert len(ingest_history(log)) == 2
        only = ingest_history(log, source="twelvedata")
        assert len(only) == 1
        assert only[0]["parser_version"] == "1"
