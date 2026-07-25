"""FRED adapter against the recorded fixture (no network — CLAUDE.md §7)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from treble.ingest.base import RawPayload
from treble.ingest.fred import FredAdapter, series_subject
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash

FIXTURE = Path(__file__).parent.parent / "fixtures" / "fred" / "sofr_2026-06-01_2026-07-24.csv"
FETCHED = datetime(2026, 7, 25, 15, 30, tzinfo=UTC)


@pytest.fixture
def adapter(tmp_path: Path) -> FredAdapter:
    return FredAdapter(
        PayloadStore(tmp_path / "payloads"),
        IngestLog(tmp_path / "log.db"),
        series=("SOFR",),
        start=FETCHED.date(),
        end=FETCHED.date(),
    )


def load_payload() -> RawPayload:
    return RawPayload(
        data=FIXTURE.read_bytes(),
        source_uri="https://fred.stlouisfed.org/graph/fredgraph.csv?id=SOFR",
        fetched_at=FETCHED,
    )


class TestParse:
    def test_parses_recorded_sofr(self, adapter: FredAdapter) -> None:
        payload = load_payload()
        batch = adapter.parse(payload, payload_hash(payload.data))
        assert batch.facts, "recorded fixture produced no facts"
        [prov] = batch.provenance
        subject = series_subject("SOFR")
        for fact in batch.facts:
            assert fact.subject == subject
            assert fact.field == "PX_LAST"
            assert fact.provenance_id == prov.id
            assert fact.knowledge_from == FETCHED  # I2: knowledge = fetch time
            assert fact.effective_from == fact.effective_to
            assert fact.value is None or isinstance(fact.value, float)
        # The fixture's first line is a real observation.
        first = batch.facts[0]
        assert first.effective_from.isoformat() == "2026-06-01"
        assert first.value == 3.65

    def test_parse_is_pure(self, adapter: FredAdapter) -> None:
        payload = load_payload()
        key = payload_hash(payload.data)
        assert adapter.parse(payload, key) == adapter.parse(payload, key)

    def test_rejects_unrecognised_payload(self, adapter: FredAdapter) -> None:
        bad = RawPayload(data=b"<html>error</html>", source_uri="x", fetched_at=FETCHED)
        with pytest.raises(ValueError):
            adapter.parse(bad, payload_hash(bad.data))


def test_run_stores_raw_before_parse_and_replays(tmp_path: Path) -> None:
    """I5 through the adapter template: raw first, replay byte-identical."""
    payloads = PayloadStore(tmp_path / "payloads")
    log = IngestLog(tmp_path / "log.db")

    class FixtureFred(FredAdapter):
        def fetch(self):  # type: ignore[override]
            yield load_payload()

    adapter = FixtureFred(payloads, log, series=("SOFR",), start=FETCHED.date(), end=FETCHED.date())
    batches = list(adapter.run())
    assert len(batches) == 1
    # The raw payload is stored and logged.
    [entry] = log.read()
    assert entry.source == "fred"
    assert payloads.get(entry.payload_hash) == FIXTURE.read_bytes()
    # Replay without network reproduces the identical batch.
    replayed = list(adapter.replay())
    assert replayed == batches
