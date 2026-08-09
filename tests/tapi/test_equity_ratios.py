"""Fundamental ratios from stored XBRL (spec §14.1).

analytics/equity/ratios.py held these since Phase 1 and nothing called
them. The two decisions worth testing are not the arithmetic -- that has
its own suite -- but which tag supplied each input, and the refusal to mix
periods.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.equity_ratios import RatiosUnavailableError, ratios_for

KNOWN = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 1, 18, 0, tzinfo=UTC)
CIK = TUID("cik:0000320193")
FY25, FY24 = date(2025, 9, 30), date(2024, 9, 30)


@pytest.fixture
def store(tmp_path: Path) -> DuckStore:
    return DuckStore(tmp_path / "t.db")


def _write(store: DuckStore, rows: list[tuple[str, float, date]]) -> None:
    prov = Provenance(
        source_system="edgar-companyfacts",
        source_uri="https://www.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        retrieved_at=KNOWN,
        method=ExtractionMethod.XBRL,
        extractor_version="1",
        payload_hash="0" * 64,
    )
    store.write_provenance([prov])
    store.write_facts(
        [
            Fact(
                subject=str(CIK),
                field=f"{tag}:USD",
                value=value,
                effective_from=period,
                effective_to=period,
                knowledge_from=KNOWN,
                provenance_id=prov.id,
            )
            for tag, value, period in rows
        ]
    )


class TestTheTagTravels:
    def test_the_tag_that_supplied_revenue_is_reported(self, store: DuckStore) -> None:
        """2,349 filers report `Revenues` and 3,184 report
        `RevenueFromContractWithCustomerExcludingAssessedTax`. A margin from
        one is not comparable with a margin from the other, so a screen
        showing only the percentage would present them as though it were."""
        _write(
            store,
            [
                ("us-gaap:Revenues", 400.0, FY25),
                ("us-gaap:NetIncomeLoss", 100.0, FY25),
            ],
        )
        result = ratios_for(store, CIK, as_of=LATER)
        assert result.ratios["net_margin"] == pytest.approx(0.25)
        assert result.sources["revenue"] == "us-gaap:Revenues"

    def test_the_more_specific_tag_wins_when_both_are_filed(self, store: DuckStore) -> None:
        """`Revenues` is whatever the filer decided to call the top line;
        the contract-revenue tag says what it measures."""
        _write(
            store,
            [
                ("us-gaap:Revenues", 400.0, FY25),
                ("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", 380.0, FY25),
                ("us-gaap:NetIncomeLoss", 100.0, FY25),
            ],
        )
        result = ratios_for(store, CIK, as_of=LATER)
        assert result.sources["revenue"].endswith("ExcludingAssessedTax")
        assert result.ratios["net_margin"] == pytest.approx(100.0 / 380.0)


class TestOnePeriodOrNothing:
    def test_a_concept_from_another_period_is_not_borrowed(self, store: DuckStore) -> None:
        """Mixing this year's income with last year's equity gives a return
        on equity wrong by however much the balance sheet moved, and wrong
        in a way no reader can see."""
        _write(
            store,
            [
                ("us-gaap:NetIncomeLoss", 100.0, FY25),
                ("us-gaap:StockholdersEquity", 500.0, FY24),
            ],
        )
        with pytest.raises(RatiosUnavailableError, match="no pair supports"):
            ratios_for(store, CIK, as_of=LATER)

    def test_absent_concepts_are_named(self, store: DuckStore) -> None:
        """A missing ratio and a ratio nobody asked for look identical on a
        screen, and only the first is a data gap."""
        _write(
            store,
            [
                ("us-gaap:Revenues", 400.0, FY25),
                ("us-gaap:NetIncomeLoss", 100.0, FY25),
            ],
        )
        result = ratios_for(store, CIK, as_of=LATER)
        assert "equity" in result.missing
        assert "return_on_equity" not in result.ratios

    def test_the_period_defaults_to_the_latest_net_income(self, store: DuckStore) -> None:
        _write(
            store,
            [
                ("us-gaap:Revenues", 300.0, FY24),
                ("us-gaap:NetIncomeLoss", 60.0, FY24),
                ("us-gaap:Revenues", 400.0, FY25),
                ("us-gaap:NetIncomeLoss", 100.0, FY25),
            ],
        )
        result = ratios_for(store, CIK, as_of=LATER)
        assert result.period == FY25
        assert result.ratios["net_margin"] == pytest.approx(0.25)


class TestItRefusesRatherThanInvents:
    def test_no_net_income_is_refused(self, store: DuckStore) -> None:
        _write(store, [("us-gaap:Revenues", 400.0, FY25)])
        with pytest.raises(RatiosUnavailableError, match="NetIncomeLoss"):
            ratios_for(store, CIK, as_of=LATER)

    def test_a_zero_denominator_omits_the_ratio(self, store: DuckStore) -> None:
        """A filing that cannot support the ratio, not a ratio of zero."""
        _write(
            store,
            [
                ("us-gaap:Revenues", 0.0, FY25),
                ("us-gaap:NetIncomeLoss", 100.0, FY25),
                ("us-gaap:Assets", 900.0, FY25),
            ],
        )
        result = ratios_for(store, CIK, as_of=LATER)
        assert "net_margin" not in result.ratios
        assert result.ratios["return_on_assets"] == pytest.approx(100.0 / 900.0)
