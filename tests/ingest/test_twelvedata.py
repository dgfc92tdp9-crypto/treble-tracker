"""Twelve Data daily equity prices (spec §9.1).

Recorded fixture, no network. The properties worth guarding here are not
"does JSON parse" — they are the ones whose failure is silent: a credential
reaching the payload store, a symbol taken from the request rather than the
response, and a row the parser did not understand becoming a price.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tests.ingest.test_parser_output_is_stable import check as check_parser_digest
from treble.ingest.base import RawPayload
from treble.ingest.twelvedata import (
    API_KEY_ENV,
    PRICE_FIELD,
    VOLUME_FIELD,
    TwelveDataDailyAdapter,
    TwelveDataError,
)
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "twelvedata" / "ibm_daily.json"
FETCHED = datetime(2026, 8, 7, 9, 30, tzinfo=UTC)
HASH = PayloadHash("0" * 64)


@pytest.fixture
def adapter(tmp_path: Path) -> TwelveDataDailyAdapter:
    return TwelveDataDailyAdapter(
        PayloadStore(tmp_path / "payloads"),
        IngestLog(tmp_path / "ingest.db"),
        symbols=("IBM",),
    )


def _payload(data: bytes | None = None, uri: str | None = None) -> RawPayload:
    return RawPayload(
        data=data if data is not None else FIXTURE.read_bytes(),
        source_uri=uri or "https://api.twelvedata.com/time_series?symbol=IBM&interval=1day",
        fetched_at=FETCHED,
    )


class TestTheCredentialNeverLeaves:
    def test_the_recorded_fixture_carries_no_key(self) -> None:
        """The fixture is committed. If the vendor ever echoed the key back
        in a response, this is where it would enter the repository."""
        text = FIXTURE.read_text()
        assert "apikey" not in text.lower()
        assert "TWELVEDATA" not in text

    def test_the_source_uri_has_no_key_in_it(self, adapter: TwelveDataDailyAdapter) -> None:
        """A credential in a stored URI is permanent: it survives into the
        payload store, the ingest log and every provenance record, and I5
        means those are replayed rather than discarded."""
        batch = adapter.parse(_payload(), HASH)
        uri = batch.provenance[0].source_uri
        assert "apikey" not in uri.lower()
        assert "symbol=IBM" in uri

    def test_fetch_refuses_without_a_key_rather_than_calling(
        self, adapter: TwelveDataDailyAdapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        with pytest.raises(TwelveDataError, match=API_KEY_ENV):
            next(adapter.fetch())


class TestParsing:
    def test_it_reads_the_recorded_series(self, adapter: TwelveDataDailyAdapter) -> None:
        batch = adapter.parse(_payload(), HASH)
        prices = [f for f in batch.facts if f.field == PRICE_FIELD]
        assert len(prices) == len(json.loads(FIXTURE.read_text())["values"])
        assert all(f.subject == "equity:IBM" for f in prices)
        assert all(isinstance(f.effective_from, date) for f in prices)

    def test_volume_travels_with_the_price(self, adapter: TwelveDataDailyAdapter) -> None:
        batch = adapter.parse(_payload(), HASH)
        assert any(f.field == VOLUME_FIELD for f in batch.facts)

    def test_the_price_field_says_it_is_adjusted(self) -> None:
        """The series is total-return (0 of 164 OHLC values on exact cents in
        2006, against 52% in 2026, with no split since 1999). A column called
        CLOSE that is silently a total return is the kind of thing a later
        reader regresses against a factor model without checking."""
        assert PRICE_FIELD == "ADJ_CLOSE"

    def test_the_knowledge_date_is_the_retrieval_not_the_clock(
        self, adapter: TwelveDataDailyAdapter
    ) -> None:
        """I2, and I5: a parse that read the wall clock would produce a
        different fact on every replay of the same bytes."""
        batch = adapter.parse(_payload(), HASH)
        assert {f.knowledge_from for f in batch.facts} == {FETCHED}

    def test_parsing_is_deterministic(self, adapter: TwelveDataDailyAdapter) -> None:
        first = adapter.parse(_payload(), HASH)
        second = adapter.parse(_payload(), HASH)
        assert first.facts == second.facts


class TestItRefusesRatherThanInvents:
    def test_a_response_with_no_series_raises_with_the_vendor_message(
        self, adapter: TwelveDataDailyAdapter
    ) -> None:
        body = json.dumps({"code": 429, "message": "API credits exceeded", "status": "error"})
        with pytest.raises(TwelveDataError, match="API credits exceeded"):
            adapter.parse(_payload(body.encode()), HASH)

    def test_a_response_with_no_symbol_is_refused(self, adapter: TwelveDataDailyAdapter) -> None:
        """Falling back to the requested symbol would let a vendor-side
        mix-up file one instrument's prices under another's subject, which
        is unrecoverable once written."""
        doc = json.loads(FIXTURE.read_text())
        doc["meta"].pop("symbol")
        with pytest.raises(TwelveDataError, match="no symbol"):
            adapter.parse(_payload(json.dumps(doc).encode()), HASH)

    def test_a_row_without_a_close_produces_no_fact(self, adapter: TwelveDataDailyAdapter) -> None:
        """Not a zero-price day — a row this parser does not understand.
        Substituting a value would put a fabricated return in the panel."""
        doc = json.loads(FIXTURE.read_text())
        doc["values"][0].pop("close")
        batch = adapter.parse(_payload(json.dumps(doc).encode()), HASH)
        prices = [f for f in batch.facts if f.field == PRICE_FIELD]
        assert len(prices) == len(doc["values"]) - 1

    def test_a_series_that_parses_to_nothing_raises(self, adapter: TwelveDataDailyAdapter) -> None:
        doc = json.loads(FIXTURE.read_text())
        doc["values"] = [{"datetime": "not-a-date", "close": "nonsense"}]
        with pytest.raises(TwelveDataError, match="no usable rows"):
            adapter.parse(_payload(json.dumps(doc).encode()), HASH)

    def test_an_empty_symbol_list_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="no symbols"):
            TwelveDataDailyAdapter(
                PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), symbols=()
            )


class TestTheParserDoesNotChangeWithoutItsVersion:
    """I5: a parser is a pure function of (payload, parser version).

    Three adapters have already changed output while keeping their version —
    `dtcc-sdr` (227 against 234 for one payload), `sec-nport` (two subject
    schemes) and `openfigi` (a moving effective date). Each was found after
    the wrong rows were in the store. This is the guard, and it is on every
    adapter rather than the three that happened to burn us.
    """

    def test_the_parse_matches_its_recorded_digest(self, adapter: TwelveDataDailyAdapter) -> None:
        batch = adapter.parse(_payload(), HASH)
        check_parser_digest("twelvedata", TwelveDataDailyAdapter.parser_version, batch)
