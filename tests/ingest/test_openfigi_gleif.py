"""OpenFIGI and GLEIF adapters against recorded fixtures (no network).

The OpenFIGI fixture is the raw response recorded 2026-07-25 for two jobs
(IBM US ticker; IBM ISIN US4592001014); the test wraps it into the
request/response envelope exactly as the adapter's fetch() does.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.ingest.test_parser_output_is_stable import check as check_parser_digest
from treble.core.identifiers import validate_figi, validate_lei
from treble.ingest.base import RawPayload
from treble.ingest.gleif import GleifAdapter, lei_subject
from treble.ingest.openfigi import MAPPING_PERIOD_START, OpenFigiAdapter, figi_subject
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash

FIXTURES = Path(__file__).parent.parent / "fixtures"
OPENFIGI_RAW = FIXTURES / "openfigi" / "mapping_ibm.json"
GLEIF_RAW = FIXTURES / "gleif" / "lei_ibm.json"
FETCHED = datetime(2026, 7, 25, 22, 45, tzinfo=UTC)
JOBS = (
    {"idType": "TICKER", "idValue": "IBM", "exchCode": "US"},
    {"idType": "ID_ISIN", "idValue": "US4592001014"},
)


@pytest.fixture
def openfigi(tmp_path: Path) -> OpenFigiAdapter:
    return OpenFigiAdapter(PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), jobs=JOBS)


@pytest.fixture
def gleif(tmp_path: Path) -> GleifAdapter:
    return GleifAdapter(PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"))


def openfigi_envelope() -> RawPayload:
    results = json.loads(OPENFIGI_RAW.read_bytes())
    envelope = json.dumps({"jobs": list(JOBS), "results": results}, sort_keys=True).encode()
    return RawPayload(
        data=envelope, source_uri="https://api.openfigi.com/v3/mapping", fetched_at=FETCHED
    )


class TestOpenFigi:
    def test_parses_recorded_mapping(self, openfigi: OpenFigiAdapter) -> None:
        raw = openfigi_envelope()
        batch = openfigi.parse(raw, payload_hash(raw.data))
        assert batch.facts
        # Every mapped FIGI must validate against the X9.145 check digit —
        # cross-validating the recorded data with our independent validator.
        figis = {f.subject for f in batch.facts if str(f.subject).startswith("figi:")}
        assert figis, "no FIGIs parsed from recorded mapping"
        for subject in figis:
            validate_figi(str(subject).removeprefix("figi:"))

    def test_ticker_job_maps_to_spec_example_figi(self, openfigi: OpenFigiAdapter) -> None:
        # The spec's own example FIGI (§9.2) is IBM's composite — the
        # recorded live mapping and the spec agree, or something is wrong.
        raw = openfigi_envelope()
        batch = openfigi.parse(raw, payload_hash(raw.data))
        subjects = {str(f.subject) for f in batch.facts}
        assert "figi:BBG000BLNNH6" in subjects

    def test_mapping_edges_recorded(self, openfigi: OpenFigiAdapter) -> None:
        raw = openfigi_envelope()
        batch = openfigi.parse(raw, payload_hash(raw.data))
        isin_edges = [f for f in batch.facts if f.field == "openfigi:mapped:ID_ISIN"]
        assert any(f.value == "US4592001014" for f in isin_edges)

    def test_parse_is_pure(self, openfigi: OpenFigiAdapter) -> None:
        raw = openfigi_envelope()
        key = payload_hash(raw.data)
        assert openfigi.parse(raw, key) == openfigi.parse(raw, key)

    def test_rejects_bare_response_without_jobs(self, openfigi: OpenFigiAdapter) -> None:
        bare = RawPayload(data=OPENFIGI_RAW.read_bytes(), source_uri="x", fetched_at=FETCHED)
        with pytest.raises(ValueError):
            openfigi.parse(bare, payload_hash(bare.data))


class TestGleif:
    def test_parses_recorded_record(self, gleif: GleifAdapter) -> None:
        raw = RawPayload(
            data=GLEIF_RAW.read_bytes(),
            source_uri="https://api.gleif.org/api/v1/lei-records?filter...",
            fetched_at=FETCHED,
        )
        batch = gleif.parse(raw, payload_hash(raw.data))
        assert batch.facts
        # The recorded LEI must pass our independent ISO 17442 checksum.
        leis = {str(f.subject).removeprefix("lei:") for f in batch.facts}
        assert leis
        for lei in leis:
            validate_lei(lei)
        # And the record is genuinely IBM's.
        names = [f.value for f in batch.facts if f.field == "gleif:legalName"]
        assert any("INTERNATIONAL BUSINESS MACHINES" in str(n) for n in names)

    def test_subject_key_form(self) -> None:
        assert str(lei_subject("abc")) == "lei:ABC"
        assert str(figi_subject("bbg000blnnh6")) == "figi:BBG000BLNNH6"


class TestAMappingIsNotAFactAboutADay:
    """A FIGI never changes (CLAUDE.md §9.3), so it must not be filed under
    the day it was fetched.

    It was. `effective_from` was `payload.fetched_at.date()`, so re-fetching
    the *same* content-addressed payload minted a new effective period —
    a different partition, which write-path coalescing cannot collapse
    because a different `effective_from` is a different assertion. On the
    live store that put 5,877 mappings in 17,631 rows across two fetch
    dates, and `subject_facts` on a FIGI subject returned every field twice.
    """

    def test_mappings_are_filed_under_the_stable_period(self, openfigi: OpenFigiAdapter) -> None:
        batch = openfigi.parse(openfigi_envelope(), payload_hash(openfigi_envelope().data))
        mappings = [f for f in batch.facts if str(f.subject).startswith("figi:")]
        assert mappings
        assert {f.effective_from for f in mappings} == {MAPPING_PERIOD_START}

    def test_the_period_is_open_ended(self, openfigi: OpenFigiAdapter) -> None:
        """ "From the beginning of time until further notice" is the claim.
        Closing it would say the mapping stops, which nothing knows."""
        batch = openfigi.parse(openfigi_envelope(), payload_hash(openfigi_envelope().data))
        mappings = [f for f in batch.facts if str(f.subject).startswith("figi:")]
        assert all(f.effective_to is None for f in mappings)

    def test_fetching_the_same_payload_again_adds_nothing(self, tmp_path: Path) -> None:
        """The defect, as a behaviour rather than a date. Three fetches on
        three days used to store three copies; now two of them coalesce."""
        from treble.store.duck import DuckStore

        store = DuckStore(tmp_path / "s.db")
        adapter = OpenFigiAdapter(
            PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), jobs=JOBS
        )
        raw = openfigi_envelope()
        for day in (1, 2, 3):
            when = datetime(2026, 8, day, 12, 0, tzinfo=UTC)
            batch = adapter.parse(
                RawPayload(data=raw.data, source_uri=raw.source_uri, fetched_at=when),
                payload_hash(raw.data),
            )
            store.write_provenance(list(batch.provenance))
            store.write_facts(list(batch.facts))
        after_one = len(adapter.parse(raw, payload_hash(raw.data)).facts)
        assert store.fact_count() == after_one, "a repeat fetch minted new partitions"
        assert store.coalesced == 2 * after_one

    def test_a_figi_subject_returns_each_field_once(self, tmp_path: Path) -> None:
        """What the duplication actually looked like from a reader's side."""
        from collections import Counter

        from treble.store.duck import DuckStore

        store = DuckStore(tmp_path / "s.db")
        adapter = OpenFigiAdapter(
            PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), jobs=JOBS
        )
        raw = openfigi_envelope()
        for day in (1, 2):
            when = datetime(2026, 8, day, 12, 0, tzinfo=UTC)
            batch = adapter.parse(
                RawPayload(data=raw.data, source_uri=raw.source_uri, fetched_at=when),
                payload_hash(raw.data),
            )
            store.write_provenance(list(batch.provenance))
            store.write_facts(list(batch.facts))

        subject = next(
            f.subject
            for f in adapter.parse(raw, payload_hash(raw.data)).facts
            if str(f.subject).startswith("figi:")
        )
        rows = store.subject_facts(subject, as_of=datetime(2026, 9, 2, tzinfo=UTC))
        repeated = {f: n for f, n in Counter(r.field for r in rows).items() if n > 1}
        assert not repeated, f"fields returned more than once: {repeated}"

    def test_an_unmapped_identifier_keeps_the_fetch_date(self, openfigi: OpenFigiAdapter) -> None:
        """The deliberate exception. An identifier that failed to map today
        may map next month, so that *is* a fact about a day — and filing it
        as timeless would say the thing can never be mapped."""
        envelope = json.dumps(
            {
                "jobs": [{"idType": "ID_ISIN", "idValue": "XX0000000000"}],
                "results": [{"error": "No identifier found."}],
            },
            sort_keys=True,
        ).encode()
        raw = RawPayload(
            data=envelope, source_uri="https://api.openfigi.com/v3/mapping", fetched_at=FETCHED
        )
        (fact,) = openfigi.parse(raw, payload_hash(envelope)).facts
        assert fact.field == "openfigi:error"
        assert fact.effective_from == FETCHED.date()
        assert fact.effective_from != MAPPING_PERIOD_START

    def test_the_parse_matches_its_recorded_digest(self, openfigi: OpenFigiAdapter) -> None:
        raw = openfigi_envelope()
        batch = openfigi.parse(raw, payload_hash(raw.data))
        check_parser_digest("openfigi", OpenFigiAdapter.parser_version, batch)
