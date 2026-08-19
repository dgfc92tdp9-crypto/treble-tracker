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


class TestPayloadsAreStoredCompressed:
    """Compression must be invisible above `PayloadStore` (I5).

    **The key is the hash of the *original* bytes, not the compressed
    form.** Hashing the compressed form would have been simpler and would
    have invalidated every `provenance.payload_hash` already stored — facts
    pointing at addresses that no longer resolve — and would make the
    address depend on the compression level, so re-compressing a payload
    would move it. The address is a property of the source's bytes.
    """

    @staticmethod
    def _store(tmp_path: Path) -> object:
        from treble.store.payloads import PayloadStore

        return PayloadStore(tmp_path / "payloads")

    def test_the_key_is_the_hash_of_the_original_bytes(self, tmp_path: Path) -> None:
        from treble.store.payloads import payload_hash

        body = b'{"values": [1, 2, 3]}'
        assert self._store(tmp_path).put(body) == payload_hash(body)  # type: ignore[attr-defined]

    def test_the_original_bytes_come_back_byte_for_byte(self, tmp_path: Path) -> None:
        """I5 replay re-parses these; anything but an exact round trip would
        produce a different fact set from the same source."""
        store = self._store(tmp_path)
        body = b"Date,1 Mo\n08/07/2026,3.79\n" * 50
        assert store.get(store.put(body)) == body  # type: ignore[attr-defined]

    def test_it_is_actually_smaller_on_disk(self, tmp_path: Path) -> None:
        """The point of the exercise. Asserted on compressible text, since
        that is what the store holds — EDGAR XML, N-PORT filings, CSV."""
        store = self._store(tmp_path)
        body = b'{"cik":"0000051143","val":1234567}' * 500
        store.put(body)  # type: ignore[attr-defined]
        on_disk = sum(p.stat().st_size for p in (tmp_path / "payloads").rglob("*.gz"))
        assert on_disk < len(body) / 5

    def test_a_corrupt_archive_raises_rather_than_returning_garbage(self, tmp_path: Path) -> None:
        """A truncated archive is an I5 failure: the facts parsed from that
        payload can no longer be reproduced, and returning partial bytes
        would let a replay produce a different fact set silently."""
        from treble.store.payloads import PayloadIntegrityError

        store = self._store(tmp_path)
        key = store.put(b"some source document")  # type: ignore[attr-defined]
        next((tmp_path / "payloads").rglob("*.gz")).write_bytes(b"not a gzip stream")
        with pytest.raises(PayloadIntegrityError, match="could not be decompressed"):
            store.get(key)  # type: ignore[attr-defined]

    def test_a_legacy_uncompressed_payload_is_still_readable(self, tmp_path: Path) -> None:
        """A store part-way through migration must answer for every payload
        it holds. This is what makes the migration interruptible."""
        from treble.store.payloads import payload_hash

        store = self._store(tmp_path)
        body = b"written before compression existed"
        key = payload_hash(body)
        legacy = tmp_path / "payloads" / key[:2] / key[2:4] / key
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_bytes(body)
        assert store.exists(key)  # type: ignore[attr-defined]
        assert store.get(key) == body  # type: ignore[attr-defined]

    def test_migration_verifies_before_destroying_the_original(self, tmp_path: Path) -> None:
        """One file at a time, read back through the public path, and only
        then unlinked — so an interruption leaves either the original or a
        verified replacement, never neither. The reason for running this is
        usually a full disk, so needing room for a second copy of the whole
        store would make it unusable exactly when it is needed."""
        from treble.store.payloads import payload_hash

        store = self._store(tmp_path)
        body = b"a source document" * 100
        key = payload_hash(body)
        legacy = tmp_path / "payloads" / key[:2] / key[2:4] / key
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_bytes(body)

        files, before, after = store.compress_existing()  # type: ignore[attr-defined]
        assert files == 1
        assert before == len(body)
        assert after < before
        assert not legacy.exists()
        assert store.get(key) == body  # type: ignore[attr-defined]

    def test_migration_refuses_a_payload_that_fails_its_own_address(self, tmp_path: Path) -> None:
        """Compressing a damaged payload would hide the damage behind a
        fresh archive that decompresses cleanly to the wrong bytes."""
        from treble.store.payloads import PayloadIntegrityError, payload_hash

        store = self._store(tmp_path)
        key = payload_hash(b"the real bytes")
        wrong = tmp_path / "payloads" / key[:2] / key[2:4] / key
        wrong.parent.mkdir(parents=True, exist_ok=True)
        wrong.write_bytes(b"different bytes entirely")
        with pytest.raises(PayloadIntegrityError, match="does not match its own address"):
            store.compress_existing()  # type: ignore[attr-defined]

    def test_put_is_still_idempotent(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        body = b"same document twice"
        assert store.put(body) == store.put(body)  # type: ignore[attr-defined]
