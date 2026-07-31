"""ECB reference rates, from a recorded payload (CLAUDE.md §7 — offline).

The fixture is the real SDMX CSV trimmed to forty observations. Trimmed, not
synthesised: the columns under test are the ones the ECB actually emits,
including the ones nothing here reads.

These two adapters shipped without fixture tests, which the drift check
would not have caught — it verifies an adapter has *run*, not that it is
covered. That is the same shape as the finding that started the audit, so
it is closed here rather than noted.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.ingest.base import RawPayload
from treble.ingest.ecb import EcbExchangeRatesAdapter, fx_subject
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash

FIXTURE = Path(__file__).parent.parent / "fixtures" / "ecb" / "exr_usd_eur.csv"
SOURCE = "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A"
FETCHED = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


@pytest.fixture
def facts(tmp_path: Path) -> tuple:
    adapter = EcbExchangeRatesAdapter(
        PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), series=("D.USD.EUR.SP00.A",)
    )
    data = FIXTURE.read_bytes()
    raw = RawPayload(data=data, source_uri=SOURCE, fetched_at=FETCHED)
    return adapter.parse(raw, payload_hash(data)).facts


class TestSubjectNaming:
    def test_the_sdmx_key_becomes_the_pair(self) -> None:
        """A subject should name the instrument. Frequency and series type
        describe the observation, not the currency pair."""
        assert fx_subject("D.USD.EUR.SP00.A") == "fx:USDEUR"

    def test_an_unexpected_key_shape_still_yields_a_subject(self) -> None:
        """Rather than raising mid-ingest: an unparseable key is a naming
        problem, not a reason to lose the rate."""
        assert fx_subject("ODD").startswith("fx:")


class TestParsing:
    def test_observations_are_extracted(self, facts: tuple) -> None:
        assert len(facts) >= 30

    def test_every_fact_is_a_rate_for_the_pair(self, facts: tuple) -> None:
        assert {f.subject for f in facts} == {"fx:USDEUR"}
        assert {f.field for f in facts} == {"PX_LAST"}

    def test_rates_are_plausible_for_the_pair(self, facts: tuple) -> None:
        """USD per EUR has not been outside 0.8-1.7 in the euro's lifetime.
        A units or column slip would land far outside that."""
        assert all(0.8 < float(f.value) < 1.7 for f in facts)  # type: ignore[arg-type]

    def test_an_observation_is_an_instant_not_a_span(self, facts: tuple) -> None:
        """A daily fixing describes one day."""
        assert all(f.effective_from == f.effective_to for f in facts)

    def test_dates_are_real_dates(self, facts: tuple) -> None:
        assert all(isinstance(f.effective_from, date) for f in facts)

    def test_every_fact_carries_provenance(self, facts: tuple) -> None:
        assert all(f.provenance_id for f in facts)

    def test_the_knowledge_date_is_the_retrieval_time(self, facts: tuple) -> None:
        """The payload carries no per-observation timestamp, so retrieval is
        the honest knowledge date rather than an invented one."""
        assert all(f.knowledge_from == FETCHED for f in facts)


class TestGapsAreSkipped:
    def test_a_blank_observation_is_not_a_zero_rate(self, tmp_path: Path) -> None:
        """A blank is a euro-area holiday in the ECB's own series. Coerced to
        zero it would render as an exchange rate of nothing."""
        header, *rows = FIXTURE.read_text().splitlines()
        blanked = rows[0].rsplit(",", 1)[0] if "," in rows[0] else rows[0]
        data = "\n".join([header, *rows, blanked]).encode()
        adapter = EcbExchangeRatesAdapter(
            PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), series=()
        )
        facts = adapter.parse(
            RawPayload(data=data, source_uri=SOURCE, fetched_at=FETCHED), payload_hash(data)
        ).facts
        assert all(float(f.value) != 0.0 for f in facts)  # type: ignore[arg-type]


def test_parsing_is_pure(tmp_path: Path) -> None:
    """I5: replay depends on parse being a function of the payload alone."""
    adapter = EcbExchangeRatesAdapter(
        PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), series=()
    )
    data = FIXTURE.read_bytes()
    raw = RawPayload(data=data, source_uri=SOURCE, fetched_at=FETCHED)
    assert (
        adapter.parse(raw, payload_hash(data)).facts == adapter.parse(raw, payload_hash(data)).facts
    )
