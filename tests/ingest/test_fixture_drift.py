"""Fixture-drift detection (PROGRESS.md "Continuous verification").

Seven external feeds underpin this system. Any of them can change a field
name, drop a column, or alter a URL, and the per-commit suite would never
notice — it runs offline against payloads recorded on the day the adapter
was written. That is the correct design for reproducible tests and a
blind spot for *staleness*.

These tests re-fetch each source live and compare its **schema** (field
names and structure) against the recorded fixture. They do not compare
values: markets move, and a changed number is not a defect. A changed
*shape* is, because it means a parser is reading the wrong thing or
silently dropping data.

Skipped unless ``TREBLE_CHECK_DRIFT=1`` so the offline contract for the
normal suite holds (CLAUDE.md §7: no network in CI). The nightly deep
workflow sets it.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import zipfile
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures"

pytestmark = [
    pytest.mark.drift,
    pytest.mark.skipif(
        os.environ.get("TREBLE_CHECK_DRIFT") != "1",
        reason="live network check; set TREBLE_CHECK_DRIFT=1 (nightly deep run)",
    ),
]

EDGAR_CONTACT = os.environ.get("TREBLE_EDGAR_CONTACT", "jack_treble@icloud.com")
SEC_HEADERS = {"User-Agent": f"TrebleTracker/0.1 ({EDGAR_CONTACT})"}


def _get(url: str, headers: dict[str, str] | None = None) -> httpx.Response:
    response = httpx.get(url, headers=headers or {}, timeout=90.0, follow_redirects=True)
    response.raise_for_status()
    return response


def test_fred_csv_header_unchanged() -> None:
    recorded = (FIXTURES / "fred" / "sofr_2026-06-01_2026-07-24.csv").read_text()
    expected = next(csv.reader(io.StringIO(recorded)))
    live = _get(
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SOFR&cosd=2026-06-01&coed=2026-06-05"
    ).text
    actual = next(csv.reader(io.StringIO(live)))
    assert actual == expected, f"FRED header changed: {expected} -> {actual}"


def test_treasury_auction_fields_unchanged() -> None:
    recorded = json.loads((FIXTURES / "treasury" / "auctions_2026-06.json").read_bytes())
    expected = set(recorded["data"][0])
    live = _get(
        "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
        "/v1/accounting/od/auctions_query?page%5Bsize%5D=1"
    ).json()
    actual = set(live["data"][0])
    missing = expected - actual
    assert not missing, f"Treasury dropped fields the parser reads: {sorted(missing)}"


def test_edgar_companyfacts_shape_unchanged() -> None:
    live = _get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000051143.json", SEC_HEADERS).json()
    assert "cik" in live, "EDGAR companyfacts lost its cik key"
    assert "facts" in live and "us-gaap" in live["facts"]
    tag = next(iter(live["facts"]["us-gaap"].values()))
    unit_rows = next(iter(tag["units"].values()))
    # The four keys the parser depends on for I2 correctness.
    for key in ("end", "filed", "val"):
        assert key in unit_rows[0], f"EDGAR companyfacts row lost {key!r}"


def test_nport_holding_shape_unchanged() -> None:
    recorded = (FIXTURES / "nport" / "nport_sample.xml").read_text()
    live = _get(
        "https://www.sec.gov/Archives/edgar/data/1484018/000200032426002035/primary_doc.xml",
        SEC_HEADERS,
    ).text
    # Same document, so the tag vocabulary must be identical.
    for tag in ("cusip", "valUSD", "pctVal", "assetCat", "issuerCat", "fairValLevel"):
        assert f"<{tag}>" in live, f"N-PORT lost <{tag}> the parser reads"
    recorded_tags = set(re.findall(r"<(\w+)>", recorded))
    live_tags = set(re.findall(r"<(\w+)>", live))
    lost = recorded_tags - live_tags
    assert not lost, f"N-PORT tags disappeared since recording: {sorted(lost)}"


def test_openfigi_result_fields_unchanged() -> None:
    from treble.ingest.openfigi import _RESULT_FIELDS

    response = httpx.post(
        "https://api.openfigi.com/v3/mapping",
        json=[{"idType": "TICKER", "idValue": "IBM", "exchCode": "US"}],
        headers={"Content-Type": "application/json"},
        timeout=90.0,
    )
    response.raise_for_status()
    row = response.json()[0]["data"][0]
    missing = [f for f in _RESULT_FIELDS if f not in row]
    assert not missing, f"OpenFIGI dropped fields the parser reads: {missing}"


def test_gleif_record_shape_unchanged() -> None:
    live = _get("https://api.gleif.org/api/v1/lei-records?page%5Bsize%5D=1").json()
    attributes = live["data"][0]["attributes"]
    assert "lei" in attributes
    assert "entity" in attributes and "legalName" in attributes["entity"]
    assert "registration" in attributes


def test_gleif_rr_shape_unchanged() -> None:
    meta = _get("https://leidata.gleif.org/api/v1/concatenated-files/rr").json()
    publishes = meta.get("data") or []
    assert publishes, "GLEIF concatenated-files/rr metadata returned no publishes"
    latest = max(publishes, key=lambda p: p["content_date"])
    for key in ("id", "content_date", "cdf_version", "record_count"):
        assert key in latest, f"GLEIF RR publish metadata lost {key!r}"

    zip_bytes = _get(
        f"https://leidata.gleif.org/api/v1/concatenated-files/rr/get/{latest['id']}/zip"
    ).content
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        xml_names = [name for name in archive.namelist() if name.endswith(".xml")]
        assert len(xml_names) == 1, f"expected one XML member in the RR zip, found {xml_names}"
        # A stream read of the first chunk only — the full file decompresses
        # to ~1GB, and only the parser's element vocabulary is under test.
        with archive.open(xml_names[0]) as member:
            head = member.read(300_000).decode("utf-8", errors="replace")

    for tag in (
        "rr:RelationshipRecords",
        "rr:StartNode",
        "rr:EndNode",
        "rr:NodeIDType",
        "rr:RelationshipType",
        "rr:RelationshipStatus",
        "rr:RelationshipPeriods",
    ):
        assert f"<{tag}>" in head, f"RR-CDF lost <{tag}> the parser reads"


def test_trace_treasury_header_unchanged() -> None:
    client_id = os.environ.get("FINRA_API_CLIENT_ID")
    client_secret = os.environ.get("FINRA_API_CLIENT_SECRET")
    if not (client_id and client_secret):
        pytest.skip("FINRA credentials not configured in this environment")
    token = httpx.post(
        "https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token",
        params={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=90.0,
    ).json()["access_token"]
    live = _get(
        "https://api.finra.org/data/group/fixedIncomeMarket/name/treasuryDailyAggregates?limit=1",
        {"Authorization": f"Bearer {token}"},
    ).text
    recorded = (FIXTURES / "trace" / "treasuryDailyAggregates.csv").read_text()
    assert next(csv.reader(io.StringIO(live))) == next(csv.reader(io.StringIO(recorded)))
