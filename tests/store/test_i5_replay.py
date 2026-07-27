"""I5 kill-tests: content-addressed payloads, append-only log, deterministic replay.

The adapter-level replay test (recorded fixtures through real adapters) lands
with the ingest package; this file proves the storage mechanisms those
adapters are built on. The replay property here: facts derived from the log
via a pure parser reconstruct exactly after wiping the derived store.
"""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadIntegrityError, PayloadStore, payload_hash

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


class TestPayloadStore:
    def test_round_trip_and_idempotent_put(self, tmp_path: Path) -> None:
        store = PayloadStore(tmp_path)
        key = store.put(b"raw payload bytes")
        assert store.put(b"raw payload bytes") == key
        assert store.get(key) == b"raw payload bytes"
        assert store.exists(key)

    def test_corruption_detected_on_get(self, tmp_path: Path) -> None:
        store = PayloadStore(tmp_path)
        key = store.put(b"original")
        # Simulate on-disk corruption behind the store's back.
        (tmp_path / key[:2] / key[2:4] / key).write_bytes(b"tampered")
        with pytest.raises(PayloadIntegrityError):
            store.get(key)

    def test_key_is_content_hash(self, tmp_path: Path) -> None:
        store = PayloadStore(tmp_path)
        assert store.put(b"abc") == payload_hash(b"abc")


class TestIngestLog:
    def test_append_assigns_monotonic_seq(self, tmp_path: Path) -> None:
        log = IngestLog(tmp_path / "log.db")
        first = log.append(
            source="edgar",
            payload_hash=payload_hash(b"a"),
            source_uri="https://example.test/a",
            fetched_at=NOW,
            parser_version="1",
        )
        second = log.append(
            source="fred",
            payload_hash=payload_hash(b"b"),
            source_uri="https://example.test/b",
            fetched_at=NOW,
            parser_version="1",
        )
        assert (first.seq, second.seq) == (1, 2)
        assert [e.seq for e in log.read()] == [1, 2]
        assert log.read()[0].source_uri == "https://example.test/a"

    def test_read_up_to_seq_for_point_replay(self, tmp_path: Path) -> None:
        log = IngestLog(tmp_path / "log.db")
        for blob in (b"a", b"b", b"c"):
            log.append(
                source="s",
                payload_hash=payload_hash(blob),
                source_uri="https://example.test/x",
                fetched_at=NOW,
                parser_version="1",
            )
        assert [e.seq for e in log.read(up_to_seq=2)] == [1, 2]

    def test_log_has_no_mutation_api(self, tmp_path: Path) -> None:
        log = IngestLog(tmp_path / "log.db")
        # Ignore mutation-testing artefacts (`...__mutmut_N`): they are
        # synthesised by `make mutate`, not API surface. Real members
        # cannot carry this marker, so the invariant is unweakened.
        public = [m for m in dir(log) if not m.startswith("_") and "__mutmut_" not in m]
        assert sorted(public) == ["append", "read"]


def _pure_parser(payload: bytes, parser_version: str, prov: Provenance) -> list[Fact]:
    """A deterministic parser: same payload + version -> same facts, always."""
    price = float(payload.decode())
    return [
        Fact(
            subject=TUID("bond-1"),
            field="PX_LAST",
            value=price,
            effective_from=date(2026, 7, 24),
            knowledge_from=NOW,
            provenance_id=prov.id,
        )
    ]


@pytest.mark.replay
def test_deterministic_replay_reconstructs_identical_facts(tmp_path: Path) -> None:
    payloads = PayloadStore(tmp_path / "payloads")
    log = IngestLog(tmp_path / "log.db")

    def ingest_and_derive(store: DuckStore) -> list[Fact]:
        all_facts: list[Fact] = []
        for entry in log.read():
            raw = payloads.get(entry.payload_hash)
            prov = Provenance(
                source_system=entry.source,
                source_uri=f"payload://{entry.payload_hash}",
                retrieved_at=entry.fetched_at,
                method=ExtractionMethod.FEED,
                extractor_version=entry.parser_version,
                payload_hash=entry.payload_hash,
            )
            facts = _pure_parser(raw, entry.parser_version, prov)
            store.write_provenance([prov])
            store.write_facts(facts)
            all_facts.extend(facts)
        return all_facts

    # Original ingest: raw bytes stored *before* deriving anything.
    for blob in (b"101.25", b"101.50"):
        key = payloads.put(blob)
        log.append(
            source="quotes",
            payload_hash=key,
            source_uri="contrib://quotes/test",
            fetched_at=NOW,
            parser_version="1",
        )

    first_run = ingest_and_derive(DuckStore(tmp_path / "derived-1.db"))
    # Wipe the derived store entirely; replay from log + payloads alone.
    second_run = ingest_and_derive(DuckStore(tmp_path / "derived-2.db"))

    assert first_run == second_run
