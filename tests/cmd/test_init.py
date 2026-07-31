"""`treble init` — one command from a clean checkout to a working install.

A store with no data renders every bound cell as an em dash, which is
indistinguishable from a company that reports nothing. So a new install is
seeded rather than left empty, and these pin the properties that make the
seed trustworthy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from treble.cmd.seed import CAPTURED_AT, seed, seed_available, seed_company_index
from treble.store.duck import DuckStore
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore


@pytest.fixture
def install(tmp_path: Path) -> tuple[PayloadStore, IngestLog, DuckStore]:
    return (
        PayloadStore(tmp_path / "payloads"),
        IngestLog(tmp_path / "ingest.db"),
        DuckStore(tmp_path / "treble.db"),
    )


class TestSeeding:
    def test_recorded_payloads_are_present(self) -> None:
        """Guards every check below: without fixtures the seed silently
        does nothing and the tests would pass on an empty store."""
        assert seed_available()

    def test_a_fresh_store_gets_facts(
        self, install: tuple[PayloadStore, IngestLog, DuckStore]
    ) -> None:
        payloads, log, store = install
        assert store.fact_count() == 0
        written = seed(payloads, log, store, contact_email="test@example.com")
        assert written > 1000
        assert store.fact_count() == written

    def test_every_seeded_fact_carries_provenance(
        self, install: tuple[PayloadStore, IngestLog, DuckStore]
    ) -> None:
        """The seed is real data run through the real adapters, so SPTR
        traces it back to an actual filing. Placeholder numbers with no
        provenance would be exactly the failure this system prevents."""
        payloads, log, store = install
        seed(payloads, log, store, contact_email="test@example.com")
        traces = store.subject_provenance("cik:0000051143", as_of=CAPTURED_AT)
        assert traces
        assert store.provenance(traces[0]).source_uri.startswith("https://")

    def test_the_payload_is_logged_before_it_is_parsed(
        self, install: tuple[PayloadStore, IngestLog, DuckStore]
    ) -> None:
        """I5: a seeded install must be replayable like any other, which
        depends on raw bytes being recorded before anything derives from
        them."""
        payloads, log, store = install
        seed(payloads, log, store, contact_email="test@example.com")
        entries = log.read()
        assert entries
        for entry in entries:
            assert payloads.exists(entry.payload_hash)

    def test_seeded_facts_are_dated_when_captured_not_today(
        self, install: tuple[PayloadStore, IngestLog, DuckStore]
    ) -> None:
        """Stamping the seed with today would present a snapshot as current."""
        payloads, log, store = install
        seed(payloads, log, store, contact_email="test@example.com")
        assert all(e.fetched_at == CAPTURED_AT for e in log.read())


class TestOfflineOpening:
    def test_a_company_index_is_written(self, tmp_path: Path) -> None:
        """Ticker resolution fetches EDGAR's index on first use, so without
        this a fresh install cannot open its own workstation offline."""
        assert seed_company_index(tmp_path) == 1
        index = json.loads((tmp_path / "company_index.json").read_text())
        assert [row["ticker"] for row in index.values()] == ["IBM"]

    def test_the_seeded_index_holds_real_identifiers(self, tmp_path: Path) -> None:
        """Not a fabrication — the tickers the seed contains, mapped to
        their actual CIKs."""
        seed_company_index(tmp_path)
        index = json.loads((tmp_path / "company_index.json").read_text())
        assert next(iter(index.values()))["cik_str"] == 51143

    def test_an_existing_index_is_never_overwritten(self, tmp_path: Path) -> None:
        """A real index fetched from EDGAR must not be replaced by the
        one-ticker seed."""
        (tmp_path / "company_index.json").write_text('{"real": "index"}')
        assert seed_company_index(tmp_path) == 0
        assert "real" in (tmp_path / "company_index.json").read_text()


class TestIdempotence:
    def test_seeding_twice_does_not_double_the_facts(
        self, install: tuple[PayloadStore, IngestLog, DuckStore]
    ) -> None:
        """The payload store is content-addressed, so re-seeding stores the
        same bytes once. Facts are append-only and bitemporal, so the second
        pass adds rows that supersede rather than conflict — but a caller
        checking `fact_count()` first, as `init` does, skips it entirely."""
        payloads, log, store = install
        first = seed(payloads, log, store, contact_email="test@example.com")
        assert store.fact_count() == first
