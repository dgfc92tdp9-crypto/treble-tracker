"""Population runner: resumability, per-step commit, and failure isolation.

Runs entirely offline against the recorded fixtures by overriding each
adapter's `fetch` — the same pattern the adapter tests use — so the runner's
orchestration is proven without touching the network.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.universe import PopulationStep, load_universe_config
from treble.ingest.base import RawPayload
from treble.ingest.populate import (
    Populator,
    completed_keys,
    iter_discovered_ciks,
    uri_for_step,
)
from treble.store.duck import DuckStore
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore

FIXTURES = Path(__file__).parent.parent / "fixtures"
CONFIG = Path(__file__).parent.parent.parent / "config" / "universe.yaml"
FETCHED = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
START, END = date(2026, 6, 1), date(2026, 7, 24)
CONTACT = "jack_treble@icloud.com"


@pytest.fixture
def populator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Populator:
    """A Populator whose adapters serve recorded fixtures instead of HTTP."""
    fred_bytes = (FIXTURES / "fred" / "sofr_2026-06-01_2026-07-24.csv").read_bytes()

    def fake_fred_fetch(self) -> Iterator[RawPayload]:  # type: ignore[no-untyped-def]
        for series in self._series:
            yield RawPayload(
                data=fred_bytes,
                source_uri=uri_for_step(
                    PopulationStep(source_id="fred", key=series),
                    fred_start=START,
                    fred_end=END,
                ),
                fetched_at=FETCHED,
            )

    from treble.ingest.fred import FredAdapter

    monkeypatch.setattr(FredAdapter, "fetch", fake_fred_fetch)
    return Populator(
        payloads=PayloadStore(tmp_path / "payloads"),
        log=IngestLog(tmp_path / "log.db"),
        store=DuckStore(tmp_path / "store.db"),
        contact_email=CONTACT,
        fred_start=START,
        fred_end=END,
    )


def fred_only_spec():  # type: ignore[no-untyped-def]
    """The dev universe reduced to its FRED series — enough to prove the
    orchestration without EDGAR's 5.6 MB payloads in every test."""
    dev = load_universe_config(CONFIG).get("dev")
    return dev.model_copy(
        update={"edgar_ciks": (), "treasury_auctions_since": None, "nport_filings": ()}
    )


class TestUriMapping:
    def test_uris_come_from_the_adapters_own_constants(self) -> None:
        # A divergence here would silently break resumability by making
        # every step look outstanding forever.
        from treble.ingest.edgar import COMPANYFACTS_URL

        step = PopulationStep(source_id="edgar-companyfacts", key="51143")
        assert uri_for_step(step, fred_start=START, fred_end=END) == COMPANYFACTS_URL.format(
            cik=51143
        )

    def test_two_edgar_adapters_get_distinct_uris(self) -> None:
        facts = uri_for_step(
            PopulationStep(source_id="edgar-companyfacts", key="51143"),
            fred_start=START,
            fred_end=END,
        )
        subs = uri_for_step(
            PopulationStep(source_id="edgar-submissions", key="51143"),
            fred_start=START,
            fred_end=END,
        )
        assert facts != subs

    def test_unknown_source_raises(self) -> None:
        with pytest.raises(ValueError, match="no URI mapping"):
            uri_for_step(
                PopulationStep(source_id="invented", key="x"), fred_start=START, fred_end=END
            )


class TestRun:
    def test_populates_and_writes_facts(self, populator: Populator) -> None:
        spec = fred_only_spec()
        result = populator.run(spec)
        assert result.planned == len(spec.fred_series)
        assert result.executed == len(spec.fred_series)
        assert result.facts_written > 0
        assert result.failed == ()
        assert result.outstanding == 0

    def test_rerun_is_a_no_op(self, populator: Populator) -> None:
        """The resumability guarantee: nothing is re-fetched."""
        spec = fred_only_spec()
        first = populator.run(spec)
        second = populator.run(spec)
        assert first.executed > 0
        assert second.executed == 0
        assert second.already_done == first.planned
        assert second.facts_written == 0

    def test_interrupted_run_resumes_where_it_stopped(self, populator: Populator) -> None:
        spec = fred_only_spec()
        partial = populator.run(spec, limit=2)
        assert partial.executed == 2
        remaining = populator.outstanding(spec)
        assert len(remaining) == len(spec.fred_series) - 2

        finish = populator.run(spec)
        assert finish.executed == len(spec.fred_series) - 2
        assert populator.outstanding(spec) == []

    def test_one_failing_step_does_not_abort_the_universe(
        self, populator: Populator, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A single bad source must not lose the rest of an 8,000-filer run."""
        from treble.ingest.fred import FredAdapter

        def exploding_fetch(self) -> Iterator[RawPayload]:  # type: ignore[no-untyped-def]
            raise RuntimeError("source unavailable")
            yield  # pragma: no cover

        monkeypatch.setattr(FredAdapter, "fetch", exploding_fetch)
        result = populator.run(fred_only_spec())
        assert result.executed == 0
        assert len(result.failed) == len(fred_only_spec().fred_series)
        assert "RuntimeError" in result.failed[0][1]
        # Failed steps stay outstanding so a re-run retries them.
        assert len(populator.outstanding(fred_only_spec())) == len(fred_only_spec().fred_series)

    def test_facts_are_committed_per_step_not_batched(self, populator: Populator) -> None:
        # A run interrupted partway must keep what it completed.
        spec = fred_only_spec()
        populator.run(spec, limit=1)
        assert len(completed_keys(populator._log)) == 1


class TestDiscovery:
    def test_parses_edgar_company_index(self) -> None:
        payload = (
            b'{"0":{"cik_str":320193,"ticker":"AAPL","title":"Apple Inc."},'
            b'"1":{"cik_str":51143,"ticker":"IBM","title":"IBM"}}'
        )
        assert sorted(iter_discovered_ciks(payload)) == [51143, 320193]

    def test_discovery_is_pure(self) -> None:
        payload = b'{"0":{"cik_str":1,"ticker":"A","title":"A"}}'
        assert list(iter_discovered_ciks(payload)) == list(iter_discovered_ciks(payload))
