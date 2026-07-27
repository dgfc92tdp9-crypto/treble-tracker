"""Treasury auctions adapter against the recorded fixture, plus the
published-reference golden it feeds: the auction's own price/yield pair
repriced by our bond math (CLAUDE.md §7: bond math -> Treasury auction
results)."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.analytics._ql import BusinessDay, DayCount
from treble.analytics.bonds.pricing import price_from_yield
from treble.analytics.bonds.spec import FixedBondSpec, Frequency
from treble.ingest.base import RawPayload
from treble.ingest.treasury import TreasuryAuctionsAdapter, cusip_subject
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash

FIXTURE = Path(__file__).parent.parent / "fixtures" / "treasury" / "auctions_2026-06.json"
FETCHED = datetime(2026, 7, 25, 15, 30, tzinfo=UTC)


@pytest.fixture
def adapter(tmp_path: Path) -> TreasuryAuctionsAdapter:
    return TreasuryAuctionsAdapter(
        PayloadStore(tmp_path / "payloads"),
        IngestLog(tmp_path / "log.db"),
        since=date(2026, 6, 1),
    )


def load_payload() -> RawPayload:
    return RawPayload(
        data=FIXTURE.read_bytes(),
        source_uri="https://api.fiscaldata.treasury.gov/.../auctions_query",
        fetched_at=FETCHED,
    )


class TestParse:
    def test_parses_recorded_auctions(self, adapter: TreasuryAuctionsAdapter) -> None:
        payload = load_payload()
        batch = adapter.parse(payload, payload_hash(payload.data))
        assert batch.facts
        # Every fact's knowledge date precedes or equals fetch (I2), and is
        # derived from the auction date, not the wall clock.
        for fact in batch.facts:
            assert fact.knowledge_from <= FETCHED
        # The first record in the recorded payload is fully represented.
        first = json.loads(FIXTURE.read_bytes())["data"][0]
        subject = cusip_subject(first["cusip"])
        by_field = {f.field: f for f in batch.facts if f.subject == subject}
        assert by_field["high_price"].value == pytest.approx(float(first["high_price"]))
        assert by_field["high_yield"].value == pytest.approx(float(first["high_yield"]))
        assert by_field["int_rate"].value == pytest.approx(float(first["int_rate"]))
        assert by_field["maturity_date"].value == date.fromisoformat(first["maturity_date"])

    def test_parse_is_pure(self, adapter: TreasuryAuctionsAdapter) -> None:
        payload = load_payload()
        key = payload_hash(payload.data)
        assert adapter.parse(payload, key) == adapter.parse(payload, key)


@pytest.mark.golden
def test_bond_math_reproduces_auction_price_yield_pair() -> None:
    """The published reference: the June 2026 3-Year Note auction. Treasury
    publishes the high yield and the corresponding price; our street-convention
    solve must reproduce their price from their yield.

    Treasury computes price from yield with rounding rules published in the
    auction regulations; agreement here is asserted to a tenth of a cent per
    100, which is the printed precision's neighbourhood.
    """
    record = json.loads(FIXTURE.read_bytes())["data"][0]
    assert record["security_term"] == "3-Year"
    spec = FixedBondSpec(
        coupon=float(record["int_rate"]) / 100.0,
        frequency=Frequency.SEMIANNUAL,
        issue_date=date.fromisoformat(record["issue_date"]),
        maturity=date.fromisoformat(record["maturity_date"]),
        day_count=DayCount.ACT_ACT_ICMA,
        business_day=BusinessDay.UNADJUSTED,
        settlement_days=0,
    )
    result = price_from_yield(
        spec, float(record["high_yield"]) / 100.0, as_of=date.fromisoformat(record["issue_date"])
    )
    assert result.value == pytest.approx(float(record["high_price"]), abs=1e-3)
