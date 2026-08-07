"""Local TAPI: resolution, point-in-time reads, staleness, and the
no-silent-blank rule (spec §8.3, §9.6, §6.3).

Uses the real recorded EDGAR fixtures written through the real store, so
what the TAPI returns is what a screen would actually render.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import SecurityQuery, YellowKey
from treble.core.provenance import ExtractionMethod, Provenance
from treble.ingest.base import RawPayload
from treble.ingest.edgar import EdgarCompanyFactsAdapter
from treble.store.duck import DuckStore
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash
from treble.tapi.fields import FIELDS, UnknownFieldError
from treble.tapi.local import LocalTapi, SecurityNotFoundError, TickerIndex

FIXTURES = Path(__file__).parent.parent / "fixtures"
COMPANYFACTS = FIXTURES / "edgar" / "companyfacts_CIK0000051143.json"
COMPANY_INDEX = FIXTURES / "edgar" / "company_tickers_sample.json"
FETCHED = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
AS_OF = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)

IBM = SecurityQuery(ticker="IBM", key=YellowKey.EQUITY, venue="US")


@pytest.fixture
def tapi(tmp_path: Path) -> LocalTapi:
    """A TAPI over a store holding IBM's real recorded fundamentals."""
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
    # IBM is not in the 50-row index slice, so add it explicitly alongside
    # the real recorded rows.
    mapping = {r["ticker"]: r["cik_str"] for r in json.loads(COMPANY_INDEX.read_bytes()).values()}
    mapping["IBM"] = 51143
    return LocalTapi(store, tickers=TickerIndex(mapping))


class TestResolution:
    def test_equity_resolves_through_the_edgar_company_index(self, tapi: LocalTapi) -> None:
        assert str(tapi.resolve(IBM)) == "cik:0000051143"

    def test_unknown_ticker_is_an_error_not_a_blank(self, tapi: LocalTapi) -> None:
        with pytest.raises(SecurityNotFoundError, match="company index"):
            tapi.resolve(SecurityQuery(ticker="NOTREAL", key=YellowKey.EQUITY, venue="US"))

    def test_macro_series_resolve_as_tickers(self, tapi: LocalTapi) -> None:
        # Spec §7.4: macro series are addressable as tickers.
        series = SecurityQuery(ticker="SOFR", key=YellowKey.INDEX)
        assert str(tapi.resolve(series)) == "fred:SOFR"

    def test_uningested_namespace_says_so(self, tapi: LocalTapi) -> None:
        bond = SecurityQuery(ticker="IBM", key=YellowKey.CORP, descriptor="4.15 05/15/39")
        with pytest.raises(SecurityNotFoundError, match="not been ingested"):
            tapi.resolve(bond)


class TestFieldReads:
    def test_reads_a_real_as_reported_value(self, tapi: LocalTapi) -> None:
        doc = json.loads(COMPANYFACTS.read_bytes())
        gaap = doc["facts"]["us-gaap"]
        tag = next(t for t in ("Assets", "Liabilities") if t in gaap)
        rows = gaap[tag]["units"]["USD"]
        expected = max(rows, key=lambda r: r["end"])["val"]

        result = tapi.field(IBM, f"us-gaap:{tag}:USD", {}, as_of=AS_OF)
        assert result.value == pytest.approx(float(expected))
        # I1: every rendered value can be traced.
        assert result.provenance_id is not None

    def test_absent_value_is_null_not_zero(self, tapi: LocalTapi) -> None:
        result = tapi.field(IBM, "us-gaap:NoSuchTagWhatsoever:USD", {}, as_of=AS_OF)
        assert result.value is None
        assert result.provenance_id is None

    def test_unknown_mnemonic_raises_rather_than_blanking(self, tapi: LocalTapi) -> None:
        # A silent blank is indistinguishable from a missing value, which
        # the provenance model depends on telling apart.
        with pytest.raises(UnknownFieldError, match="field dictionary"):
            tapi.field(IBM, "INVENTED_MNEMONIC", {}, as_of=AS_OF)

    def test_model_derived_field_refuses_to_fabricate(self, tapi: LocalTapi) -> None:
        # OAS is a model output; returning a stored number here would be a
        # plausible wrong answer, the worst failure mode in this domain.
        with pytest.raises(NotImplementedError, match="model-derived"):
            tapi.field(IBM, "OAS_SPREAD_MID", {}, as_of=AS_OF)

    def test_point_in_time_read_hides_later_knowledge(self, tapi: LocalTapi) -> None:
        """I2: querying before anything was filed must return nothing."""
        long_ago = datetime(1990, 1, 1, tzinfo=UTC)
        result = tapi.field(IBM, "us-gaap:Assets:USD", {}, as_of=long_ago)
        assert result.value is None

    def test_old_values_are_marked_stale(self, tmp_path: Path, tapi: LocalTapi) -> None:
        # §6.3 makes stale marking mandatory. Reading far in the future must
        # flag the fundamentals as no longer current.
        far_future = AS_OF + timedelta(days=3650)
        result = tapi.field(IBM, "us-gaap:Assets:USD", {}, as_of=far_future)
        if result.value is not None:
            assert result.stale is True


class TestSeries:
    def test_series_returns_dated_rows_in_order(self, tapi: LocalTapi) -> None:
        rows = tapi.series(IBM, "us-gaap:Assets:USD", as_of=AS_OF)
        assert len(rows) > 4, "IBM has many years of reported assets"
        dates = [r[0] for r in rows]
        assert dates == sorted(dates)


class TestFieldDictionary:
    def test_documented_spec_mnemonics_are_present(self) -> None:
        # Only mnemonics the spec itself names (§9.6/§24).
        for mnemonic in ("PX_LAST", "CUR_MKT_CAP", "OAS_SPREAD_MID", "DUR_ADJ_OAS"):
            assert mnemonic in FIELDS

    def test_as_reported_tags_are_accepted(self) -> None:
        assert "us-gaap:Assets:USD" in FIELDS
        assert FIELDS.get("us-gaap:Assets:USD").sources == ("edgar",)

    def test_invented_mnemonics_are_rejected(self) -> None:
        assert "TOTALLY_MADE_UP" not in FIELDS

    def test_overrides_are_declared_for_model_fields(self) -> None:
        # §9.6: the override mechanism is how the analytics library is
        # exposed as data.
        oas = FIELDS.get("OAS_SPREAD_MID")
        assert "OAS_VOL_OVERRIDE" in oas.overrides
        assert oas.model_derived and oas.model_id == "bonds.oas_hull_white_lattice"

    def test_flds_search(self) -> None:
        assert any(f.mnemonic == "OAS_SPREAD_MID" for f in FIELDS.search("OAS"))


class TestTickerIndex:
    def test_built_from_the_real_recorded_index(self) -> None:
        index = TickerIndex.from_company_index(COMPANY_INDEX.read_bytes())
        assert len(index) > 0
        first = json.loads(COMPANY_INDEX.read_bytes())["0"]
        assert index.cik(first["ticker"]) == first["cik_str"]

    def test_lookup_is_case_insensitive(self) -> None:
        index = TickerIndex({"IBM": 51143})
        assert index.cik("ibm") == index.cik("IBM") == 51143


class TestTheSecurityMasterResolves:
    """WP7's master was built, tested, and consulted by nothing. This
    module's own comment said descriptor-based resolution "needs a
    security-master ... unbuilt lookup" while the lookup sat one import
    away. The reachability gate surfaced it; this is the path that uses it.
    """

    @staticmethod
    def _store_with_mapping(tmp_path: Path) -> DuckStore:
        store = DuckStore(tmp_path / "m.db")
        prov = Provenance(
            source_system="openfigi",
            source_uri="https://api.openfigi.com/v3/mapping",
            retrieved_at=datetime(2026, 3, 1, tzinfo=UTC),
            method=ExtractionMethod.API,
            extractor_version="1",
            payload_hash="0" * 64,
        )
        store.write_provenance([prov])
        store.write_facts(
            [
                Fact(
                    subject="figi:BBG000BLNNH6",
                    field=field,
                    value=value,
                    effective_from=date(2026, 1, 1),
                    effective_to=date(2026, 1, 1),
                    knowledge_from=datetime(2026, 3, 1, tzinfo=UTC),
                    provenance_id=prov.id,
                )
                for field, value in (
                    ("openfigi:mapped:ID_CUSIP", "037833100"),
                    ("openfigi:name", "APPLE INC"),
                )
            ]
        )
        return store

    def test_a_cusip_with_no_subject_resolves_through_the_master(self, tmp_path: Path) -> None:
        """Nothing wrote under cusip:037833100, but OpenFIGI mapped it to a
        FIGI that has facts. Before the master was wired in, this reported
        "that CUSIP has not been ingested" for an instrument the system
        could resolve."""
        tapi = LocalTapi(self._store_with_mapping(tmp_path))
        resolved = tapi.resolve(
            SecurityQuery(ticker="037833100", key=YellowKey.CORP, venue=None, descriptor=None)
        )
        assert str(resolved) == "figi:BBG000BLNNH6"

    def test_an_unmapped_cusip_still_reports_honestly(self, tmp_path: Path) -> None:
        """The fallback must not turn a genuine miss into a resolution."""
        tapi = LocalTapi(self._store_with_mapping(tmp_path))
        with pytest.raises(SecurityNotFoundError, match="not been ingested"):
            tapi.resolve(
                SecurityQuery(ticker="999999999", key=YellowKey.CORP, venue=None, descriptor=None)
            )
