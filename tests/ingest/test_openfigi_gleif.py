"""OpenFIGI and GLEIF adapters against recorded fixtures (no network).

The OpenFIGI fixture is the raw response recorded 2026-07-25 for two jobs
(IBM US ticker; IBM ISIN US4592001014); the test wraps it into the
request/response envelope exactly as the adapter's fetch() does.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from treble.core.identifiers import validate_figi, validate_lei
from treble.ingest.base import RawPayload
from treble.ingest.gleif import GleifAdapter, lei_subject
from treble.ingest.openfigi import OpenFigiAdapter, figi_subject
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
