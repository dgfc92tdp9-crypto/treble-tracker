"""Coinbase daily candles, from a recorded payload (CLAUDE.md §7 — offline).

The fixture is the real response trimmed to forty candles. The payload is a
bare array of numbers with nothing naming the instrument, which is the most
important thing these tests pin: the subject can only come from the URI, so
replay depends on that URI being right.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.ingest.test_parser_output_is_stable import check as check_parser_digest
from treble.ingest.base import RawPayload
from treble.ingest.coinbase import CoinbaseCandlesAdapter, crypto_subject
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash

FIXTURE = Path(__file__).parent.parent / "fixtures" / "coinbase" / "btc_usd_candles.json"
SOURCE = "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=86400"
FETCHED = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


@pytest.fixture
def facts(tmp_path: Path) -> tuple:
    adapter = CoinbaseCandlesAdapter(
        PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), products=("BTC-USD",)
    )
    data = FIXTURE.read_bytes()
    raw = RawPayload(data=data, source_uri=SOURCE, fetched_at=FETCHED)
    return adapter.parse(raw, payload_hash(data)).facts


class TestTheVenueIsPartOfTheIdentity:
    def test_the_subject_names_the_exchange(self) -> None:
        """Crypto has no consolidated tape: two exchanges' prices for one
        asset are different facts about different markets. Merged under one
        subject, latest-wins would silently pick whichever was fetched
        last."""
        assert crypto_subject("BTC-USD") == "crypto:coinbase:BTC-USD"

    def test_the_product_is_recovered_from_the_uri(self, facts: tuple) -> None:
        """The payload is a bare array of numbers. If the URI stopped
        carrying the product, replay could not name the instrument at all —
        and would do so silently."""
        assert {f.subject for f in facts} == {"crypto:coinbase:BTC-USD"}

    def test_a_different_product_yields_a_different_subject(self, tmp_path: Path) -> None:
        adapter = CoinbaseCandlesAdapter(
            PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), products=()
        )
        data = FIXTURE.read_bytes()
        raw = RawPayload(
            data=data,
            source_uri="https://api.exchange.coinbase.com/products/ETH-USD/candles?granularity=86400",
            fetched_at=FETCHED,
        )
        subjects = {f.subject for f in adapter.parse(raw, payload_hash(data)).facts}
        assert subjects == {"crypto:coinbase:ETH-USD"}


class TestCandleLayout:
    def test_close_high_and_low_are_all_captured(self, facts: tuple) -> None:
        assert {f.field for f in facts} == {"PX_LAST", "PX_HIGH", "PX_LOW"}

    def test_the_high_is_never_below_the_low(self, facts: tuple) -> None:
        """The candle tuple is positional and undocumented in the payload.
        Swapping two indices would put the low above the high, and every
        value would still look like a price."""
        by_date: dict[object, dict[str, float]] = {}
        for fact in facts:
            by_date.setdefault(fact.effective_to, {})[fact.field] = float(fact.value)  # type: ignore[arg-type]
        assert by_date
        for day, values in by_date.items():
            assert values["PX_HIGH"] >= values["PX_LOW"], f"high below low on {day}"

    def test_the_close_lies_within_the_days_range(self, facts: tuple) -> None:
        """Catches an index slip that a high/low check alone would not."""
        by_date: dict[object, dict[str, float]] = {}
        for fact in facts:
            by_date.setdefault(fact.effective_to, {})[fact.field] = float(fact.value)  # type: ignore[arg-type]
        for day, v in by_date.items():
            assert v["PX_LOW"] <= v["PX_LAST"] <= v["PX_HIGH"], f"close outside range on {day}"

    def test_a_candle_covers_one_day(self, facts: tuple) -> None:
        assert all(f.effective_from == f.effective_to for f in facts)

    def test_timestamps_become_real_dates(self, facts: tuple) -> None:
        """The payload gives epoch seconds; a unit slip would land in 1970
        or far in the future."""
        assert all(2000 < f.effective_to.year < 2100 for f in facts)


class TestRobustness:
    def test_a_malformed_candle_is_skipped_not_guessed(self, tmp_path: Path) -> None:
        data = json.dumps([[1, 2], *json.loads(FIXTURE.read_bytes())[:3]]).encode()
        adapter = CoinbaseCandlesAdapter(
            PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), products=()
        )
        facts = adapter.parse(
            RawPayload(data=data, source_uri=SOURCE, fetched_at=FETCHED), payload_hash(data)
        ).facts
        assert len(facts) == 9  # three good candles, three fields each

    def test_every_fact_carries_provenance(self, facts: tuple) -> None:
        assert all(f.provenance_id for f in facts)

    def test_parsing_is_pure(self, tmp_path: Path) -> None:
        adapter = CoinbaseCandlesAdapter(
            PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), products=()
        )
        data = FIXTURE.read_bytes()
        raw = RawPayload(data=data, source_uri=SOURCE, fetched_at=FETCHED)
        assert (
            adapter.parse(raw, payload_hash(data)).facts
            == adapter.parse(raw, payload_hash(data)).facts
        )


class TestTheParserDoesNotChangeWithoutItsVersion:
    """I5: a parser is a pure function of (payload, parser version).

    Three adapters have already changed output while keeping their version —
    `dtcc-sdr` (227 against 234 for one payload), `sec-nport` (two subject
    schemes) and `openfigi` (a moving effective date). Each was found after
    the wrong rows were in the store. This is the guard, and it is on every
    adapter rather than the three that happened to burn us.
    """

    def test_the_parse_matches_its_recorded_digest(self, tmp_path: Path) -> None:
        data = FIXTURE.read_bytes()
        adapter = CoinbaseCandlesAdapter(
            PayloadStore(tmp_path / "p"),
            IngestLog(tmp_path / "l.db"),
            products=("BTC-USD",),
        )
        raw = RawPayload(data=data, source_uri=SOURCE, fetched_at=FETCHED)
        batch = adapter.parse(raw, payload_hash(data))
        check_parser_digest("coinbase", CoinbaseCandlesAdapter.parser_version, batch)
