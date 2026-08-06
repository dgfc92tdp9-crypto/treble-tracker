"""Coinbase product reference data, offline (CLAUDE.md §7).

The ticker plant and the candle series both carry `crypto:coinbase:*`
subjects, and until this adapter the store held nothing about them but
prices. The test that matters most is not that the fields arrive — it is
`test_trading_status_is_stored`: a delisted product keeps its price history,
so without a status an instrument the venue stopped trading looks exactly
like one it still trades, and the last print reads as a current price rather
than a final one.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.ingest.base import RawPayload
from treble.ingest.coinbase import (
    PRODUCTS_URL,
    CoinbaseProductsAdapter,
    crypto_subject,
)
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash

FIXTURES = Path(__file__).parent.parent / "fixtures" / "coinbase"
FETCHED = datetime(2026, 8, 6, 15, 0, tzinfo=UTC)


def adapter(tmp_path: Path) -> CoinbaseProductsAdapter:
    return CoinbaseProductsAdapter(
        PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), products=("BTC-USD",)
    )


def parse(tmp_path: Path, product: str = "BTC-USD") -> tuple[Fact, ...]:
    data = (FIXTURES / f"product_{product}.json").read_bytes()
    payload = RawPayload(
        data=data, source_uri=PRODUCTS_URL.format(product=product), fetched_at=FETCHED
    )
    return adapter(tmp_path).parse(payload, payload_hash(data)).facts


def field_of(facts: tuple[Fact, ...], name: str) -> object:
    return next(fact.value for fact in facts if fact.field == name)


class TestWhatArrives:
    def test_the_instrument_gets_a_name_and_a_tick(self, tmp_path: Path) -> None:
        facts = parse(tmp_path)
        assert field_of(facts, "SECURITY_NAME") == "BTC-USD"
        assert field_of(facts, "TICK_SIZE") == pytest.approx(0.01)

    def test_the_subject_matches_the_price_series(self, tmp_path: Path) -> None:
        """Reference data attached to a different subject from the prices
        would leave both halves present and neither joinable."""
        facts = parse(tmp_path)
        assert {fact.subject for fact in facts} == {crypto_subject("BTC-USD")}

    def test_the_subject_comes_from_the_document_not_the_url(self, tmp_path: Path) -> None:
        """A redirect or a typo in the requested product would otherwise
        attach one instrument's reference data to another's subject."""
        data = json.dumps(
            {**json.loads((FIXTURES / "product_BTC-USD.json").read_text()), "id": "ETH-USD"}
        ).encode()
        payload = RawPayload(
            data=data, source_uri=PRODUCTS_URL.format(product="BTC-USD"), fetched_at=FETCHED
        )
        facts = adapter(tmp_path).parse(payload, payload_hash(data)).facts
        assert {f.subject for f in facts} == {crypto_subject("ETH-USD")}

    def test_increments_are_numbers_not_strings(self, tmp_path: Path) -> None:
        """They arrive as decimal strings. Compared as text, "0.1" sorts
        below "0.01" and a tick-size check silently inverts."""
        facts = parse(tmp_path)
        assert isinstance(field_of(facts, "TICK_SIZE"), float)
        assert isinstance(field_of(facts, "crypto:base_increment"), float)

    def test_the_quote_and_base_currencies_are_recorded(self, tmp_path: Path) -> None:
        """A price of 64,402.48 means nothing without the currency it is in."""
        facts = parse(tmp_path)
        assert field_of(facts, "crypto:base_currency") == "BTC"
        assert field_of(facts, "crypto:quote_currency") == "USD"


class TestTradingStatus:
    def test_trading_status_is_stored(self, tmp_path: Path) -> None:
        """The test this adapter exists for. A delisted product keeps its
        price history; without a status it looks like one that still
        trades."""
        facts = parse(tmp_path)
        assert field_of(facts, "crypto:status") == "online"
        assert field_of(facts, "crypto:trading_disabled") is False

    def test_a_disabled_product_says_so(self, tmp_path: Path) -> None:
        base = json.loads((FIXTURES / "product_BTC-USD.json").read_text())
        data = json.dumps({**base, "status": "delisted", "trading_disabled": True}).encode()
        payload = RawPayload(
            data=data, source_uri=PRODUCTS_URL.format(product="BTC-USD"), fetched_at=FETCHED
        )
        facts = adapter(tmp_path).parse(payload, payload_hash(data)).facts
        assert field_of(facts, "crypto:status") == "delisted"
        assert field_of(facts, "crypto:trading_disabled") is True

    def test_a_missing_flag_is_absent_not_false(self, tmp_path: Path) -> None:
        """ "Not disabled" and "the venue did not say" are different, and only
        one of them means the instrument trades."""
        base = json.loads((FIXTURES / "product_BTC-USD.json").read_text())
        base.pop("trading_disabled")
        data = json.dumps(base).encode()
        payload = RawPayload(
            data=data, source_uri=PRODUCTS_URL.format(product="BTC-USD"), fetched_at=FETCHED
        )
        facts = adapter(tmp_path).parse(payload, payload_hash(data)).facts
        assert "crypto:trading_disabled" not in {fact.field for fact in facts}


class TestRefusals:
    def test_a_document_with_no_id_is_refused(self, tmp_path: Path) -> None:
        data = json.dumps({"base_currency": "BTC"}).encode()
        payload = RawPayload(
            data=data, source_uri=PRODUCTS_URL.format(product="BTC-USD"), fetched_at=FETCHED
        )
        with pytest.raises(ValueError, match="names no instrument"):
            adapter(tmp_path).parse(payload, payload_hash(data))

    def test_a_document_with_no_recognised_fields_is_refused(self, tmp_path: Path) -> None:
        """A schema change must fail loudly. Storing whatever still parsed
        would leave an instrument described by a fraction of its record with
        nothing saying so."""
        data = json.dumps({"id": "BTC-USD", "something_new": 1}).encode()
        payload = RawPayload(
            data=data, source_uri=PRODUCTS_URL.format(product="BTC-USD"), fetched_at=FETCHED
        )
        with pytest.raises(ValueError, match="schema has changed"):
            adapter(tmp_path).parse(payload, payload_hash(data))

    def test_an_empty_universe_is_refused_at_construction(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="empty universe"):
            CoinbaseProductsAdapter(
                PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), products=()
            )


def test_reference_data_is_dated_by_retrieval(tmp_path: Path) -> None:
    """A tick size has no observation date of its own — it is what it is
    until the venue changes it — so the retrieval day is the honest effective
    date and a later fetch supersedes it bitemporally (I2)."""
    facts = parse(tmp_path)
    assert {fact.effective_from for fact in facts} == {FETCHED.date()}
    assert {fact.knowledge_from for fact in facts} == {FETCHED}


def test_replay_reproduces_the_same_facts(tmp_path: Path) -> None:
    payloads, log = PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db")
    source = CoinbaseProductsAdapter(payloads, log, products=("BTC-USD",))
    data = (FIXTURES / "product_BTC-USD.json").read_bytes()
    key = payloads.put(data)
    log.append(
        source=source.meta.source_id,
        payload_hash=key,
        source_uri=PRODUCTS_URL.format(product="BTC-USD"),
        fetched_at=FETCHED,
        parser_version=source.parser_version,
    )
    replayed = list(source.replay())
    assert len(replayed) == 1
    assert replayed[0].facts == parse(tmp_path / "other")
