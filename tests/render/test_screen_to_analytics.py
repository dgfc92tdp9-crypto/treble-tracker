"""What a screen shows must equal what the analytics compute.

The Phase 1 audit found this gap: conformance pins a screen's *layout* and
the analytics tests pin their *values*, and nothing joined them. A screen
could bind a cell to the wrong mnemonic, or format a figure into the wrong
scale, and both suites would stay green — layout unchanged, analytics
correct, the number on screen wrong.

**These are written so they cannot be tautologies.** The expected value is
never obtained through the resolver, TAPI, or the field dictionary. It comes
either from the source document read directly, or from calling the analytic
itself. If the screen path and the independent path ever disagree, one of
them is wrong and this says so.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.analytics._ql import DayCount
from treble.analytics.bonds.pricing import modified_duration, yield_from_price
from treble.analytics.bonds.spec import FixedBondSpec, Frequency
from treble.core.facts import Fact
from treble.core.identifiers import parse_security
from treble.core.provenance import ExtractionMethod, Provenance
from treble.ingest.base import RawPayload
from treble.ingest.edgar import EdgarCompanyFactsAdapter
from treble.render.contract.registry import get_screen
from treble.render.contract.resolver import ScreenContext, resolve
from treble.store.duck import DuckStore
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash
from treble.tapi.local import LocalTapi, TickerIndex

FIXTURES = Path(__file__).parent.parent / "fixtures"
COMPANYFACTS = FIXTURES / "edgar" / "companyfacts_CIK0000051143.json"
FETCHED = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
AS_OF = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


@pytest.fixture
def tapi(tmp_path: Path) -> LocalTapi:
    store = DuckStore(tmp_path / "t.db")
    adapter = EdgarCompanyFactsAdapter(
        PayloadStore(tmp_path / "p"),
        IngestLog(tmp_path / "l.db"),
        ciks=(51143,),
        contact_email="jack_treble@icloud.com",
    )
    raw = RawPayload(data=COMPANYFACTS.read_bytes(), source_uri="fixture://cf", fetched_at=FETCHED)
    batch = adapter.parse(raw, payload_hash(raw.data))
    store.write_provenance(list(batch.provenance))
    store.write_facts(list(batch.facts))
    return LocalTapi(store, tickers=TickerIndex({"IBM": 51143}))


def _cells(
    tapi: LocalTapi, mnemonic: str, security: str | None, tab: str | None = None
) -> set[str]:
    context = ScreenContext(security=parse_security(security) if security else None, tab=tab)
    buffer = resolve(get_screen(mnemonic), context, as_of=AS_OF, tapi=tapi)
    return {cell.text.strip() for cell in buffer.cells}


def _latest_from_the_filing(tag: str, unit: str = "USD") -> float:
    """The figure read straight out of the source document.

    Deliberately not through TAPI or the field dictionary: if the screen and
    the filing disagree, something between them is wrong, and routing both
    sides through the same code would hide exactly that.
    """
    document = json.loads(COMPANYFACTS.read_bytes())
    rows = document["facts"]["us-gaap"][tag]["units"][unit]
    # Ordered by (end, start), matching how the store breaks a tie. One end
    # date carries several periods — IBM's 2026-06-30 has both a half-year
    # (3,381,000,000) and a quarter (2,165,000,000) — so "latest" alone is
    # ambiguous, and a comparison that picked differently from the store
    # would fail for a reason that has nothing to do with the screen.
    latest = max(rows, key=lambda r: (r["end"], r.get("start") or r["end"]))
    return float(latest["val"])


class TestFundamentalsReachTheScreen:
    @pytest.mark.parametrize("tag", ["Assets", "Liabilities", "StockholdersEquity"])
    def test_des_shows_the_filed_figure(self, tapi: LocalTapi, tag: str) -> None:
        """Each balance-sheet line on DES must equal what IBM filed."""
        expected = f"{_latest_from_the_filing(tag):,.0f}"
        assert expected in _cells(tapi, "DES", "IBM US Equity")

    def test_fa_income_shows_the_filed_figure(self, tapi: LocalTapi) -> None:
        expected = f"{_latest_from_the_filing('NetIncomeLoss'):,.0f}"
        assert expected in _cells(tapi, "FA", "IBM US Equity", tab="income")

    def test_fa_and_des_agree_on_a_shared_tag(self, tapi: LocalTapi) -> None:
        """Two screens binding the same tag must render the same number.
        Nothing else checks this: each screen has its own conformance case,
        and a case only compares a screen against itself."""
        assets = f"{_latest_from_the_filing('Assets'):,.0f}"
        assert assets in _cells(tapi, "DES", "IBM US Equity")
        assert assets in _cells(tapi, "FA", "IBM US Equity", tab="balance")

    def test_a_wrong_scale_would_be_caught(self, tapi: LocalTapi) -> None:
        """Guards the checks above: they only mean something if a figure
        formatted at the wrong scale would fail them."""
        assets = _latest_from_the_filing("Assets")
        assert f"{assets / 1_000_000:,.0f}" not in _cells(tapi, "DES", "IBM US Equity")


class TestComputedValuesReachTheScreen:
    """The other half: a model output must arrive on screen unaltered."""

    @staticmethod
    def _bond_tapi(tmp_path: Path) -> LocalTapi:
        store = DuckStore(tmp_path / "b.db")
        record = Provenance(
            source_system="treasury",
            source_uri="https://api.fiscaldata.treasury.gov/x",
            retrieved_at=AS_OF,
            method=ExtractionMethod.API,
            extractor_version="1",
        )
        store.write_provenance([record])
        terms = {
            "int_rate": 4.625,
            "high_price": 96.735962,
            "maturity_date": date(2046, 2, 15),
            "dated_date": date(2026, 2, 15),
            "issue_date": date(2026, 4, 30),
            "inflation_index_security": "No",
        }
        store.write_facts(
            [
                Fact(
                    subject="cusip:912810UT3",
                    field=field,
                    value=value,  # type: ignore[arg-type]
                    effective_from=date(2026, 4, 23),
                    knowledge_from=datetime(2026, 4, 24, tzinfo=UTC),
                    provenance_id=record.id,
                )
                for field, value in terms.items()
            ]
        )
        return LocalTapi(store)

    @staticmethod
    def _spec() -> FixedBondSpec:
        return FixedBondSpec(
            coupon=0.04625,
            issue_date=date(2026, 2, 15),
            maturity=date(2046, 2, 15),
            frequency=Frequency.SEMIANNUAL,
            day_count=DayCount.ACT_ACT_ICMA,
            settlement_days=0,
        )

    def test_yas_yield_equals_the_analytic(self, tmp_path: Path) -> None:
        """Computed here by calling the model directly, so the screen and the
        analytics are compared rather than the screen compared to itself."""
        computed = yield_from_price(self._spec(), 96.735962, as_of=date(2026, 4, 30)).value * 100
        rendered = _cells(self._bond_tapi(tmp_path), "YAS", "912810UT3 Govt")
        assert f"{computed:>12,.4f}".strip() in rendered

    def test_yas_duration_equals_the_analytic(self, tmp_path: Path) -> None:
        quoted = yield_from_price(self._spec(), 96.735962, as_of=date(2026, 4, 30)).value
        computed = modified_duration(self._spec(), quoted, as_of=date(2026, 4, 30)).value
        rendered = _cells(self._bond_tapi(tmp_path), "YAS", "912810UT3 Govt")
        assert f"{computed:>12,.4f}".strip() in rendered

    def test_the_screen_agrees_with_the_issuer(self, tmp_path: Path) -> None:
        """Treasury published 4.883% for this auction price. The number on
        screen must be that number, not merely one the code agrees with."""
        rendered = _cells(self._bond_tapi(tmp_path), "YAS", "912810UT3 Govt")
        assert any(cell.startswith("4.883") for cell in rendered)
