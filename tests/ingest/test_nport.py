"""N-PORT adapter against a real recorded filing (no network).

Fixture: primary_doc.xml from accession 0002000324-26-002035 (CIK 1484018),
recorded 2026-07-26. Assertions read expected values out of the XML itself,
so the test verifies the parse rather than hand-typed numbers.
"""

import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.identifiers import validate_lei
from treble.ingest.base import RawPayload
from treble.ingest.nport import NportAdapter, holding_subject
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash

FIXTURE = Path(__file__).parent.parent / "fixtures" / "nport" / "nport_sample.xml"
FETCHED = datetime(2026, 7, 26, 18, 0, tzinfo=UTC)
CONTACT = "jack_treble@icloud.com"


@pytest.fixture
def adapter(tmp_path: Path) -> NportAdapter:
    return NportAdapter(
        PayloadStore(tmp_path / "p"),
        IngestLog(tmp_path / "l.db"),
        filings=((1484018, "0002000324-26-002035"),),
        contact_email=CONTACT,
    )


def payload() -> RawPayload:
    return RawPayload(
        data=FIXTURE.read_bytes(),
        source_uri="https://www.sec.gov/Archives/edgar/data/1484018/.../primary_doc.xml",
        fetched_at=FETCHED,
    )


class TestParse:
    def test_parses_every_holding(self, adapter: NportAdapter) -> None:
        raw = payload()
        batch = adapter.parse(raw, payload_hash(raw.data))
        holdings = len(re.findall(r"<invstOrSec>", FIXTURE.read_text()))
        subjects = {f.subject for f in batch.facts}
        assert holdings > 100, "fixture should carry a real portfolio"
        # Every holding with an identifier becomes its own subject.
        assert len(subjects) >= holdings - 5

    def test_corporate_bond_fields_match_source(self, adapter: NportAdapter) -> None:
        raw = payload()
        batch = adapter.parse(raw, payload_hash(raw.data))
        # The first corporate bond in the filing, read from the XML.
        block = re.search(
            r"<invstOrSec>(?:(?!</invstOrSec>).)*?<issuerCat>CORP</issuerCat>.*?</invstOrSec>",
            FIXTURE.read_text(),
            re.S,
        )
        assert block is not None, "fixture has no corporate holding"
        text = block.group(0)

        def tag(name: str) -> str:
            m = re.search(rf"<{name}>([^<]*)</{name}>", text)
            return m.group(1).strip() if m else ""

        isin_m = re.search(r'<isin value="([^"]+)"', text)
        subject = holding_subject(cusip=tag("cusip"), isin=isin_m.group(1) if isin_m else "")
        by_field = {f.field: f.value for f in batch.facts if f.subject == subject}
        assert by_field["nport:valUSD"] == pytest.approx(float(tag("valUSD")))
        assert by_field["nport:balance"] == pytest.approx(float(tag("balance")))
        assert by_field["nport:issuerCat"] == "CORP"
        assert by_field["nport:assetCat"] == "DBT"
        # ASC 820 level drives TVAL input quality (spec §15.2).
        assert by_field["nport:fairValLevel"] in {"1", "2", "3"}
        # Debt terms come through for the analytics layer.
        assert isinstance(by_field["nport:maturityDt"], date)
        assert by_field["nport:annualizedRt"] == pytest.approx(float(tag("annualizedRt")))

    def test_implied_price_is_computable_and_sane(self, adapter: NportAdapter) -> None:
        """valUSD / balance * 100 must land in a plausible bond-price range —
        the whole point of this source. Ingest stores the inputs; this asserts
        they are consistent enough for the analytics layer to divide."""
        raw = payload()
        batch = adapter.parse(raw, payload_hash(raw.data))
        by_subject: dict[object, dict[str, object]] = {}
        for fact in batch.facts:
            by_subject.setdefault(fact.subject, {})[fact.field] = fact.value
        priced = 0
        for fields in by_subject.values():
            if fields.get("nport:assetCat") != "DBT":
                continue
            val, bal = fields.get("nport:valUSD"), fields.get("nport:balance")
            if not (isinstance(val, float) and isinstance(bal, float) and bal > 0):
                continue
            price = val / bal * 100.0
            assert 1.0 < price < 300.0, f"implausible implied price {price}"
            priced += 1
        assert priced > 10, "fixture should price many debt holdings"

    def test_na_becomes_null_not_a_string(self, adapter: NportAdapter) -> None:
        # The fixture's first holding has <lei>N/A</lei>: absence must be
        # null with provenance, never the literal "N/A" (working agreement).
        raw = payload()
        batch = adapter.parse(raw, payload_hash(raw.data))
        leis = [f.value for f in batch.facts if f.field == "nport:lei"]
        assert leis, "no LEI facts emitted"
        assert all(v != "N/A" for v in leis)
        # Any LEI that *is* present must pass ISO 17442.
        for value in leis:
            if isinstance(value, str) and value:
                validate_lei(value)

    def test_parse_is_pure(self, adapter: NportAdapter) -> None:
        raw = payload()
        key = payload_hash(raw.data)
        assert adapter.parse(raw, key) == adapter.parse(raw, key)

    def test_rejects_non_nport(self, adapter: NportAdapter) -> None:
        bad = RawPayload(data=b"<root/>", source_uri="x", fetched_at=FETCHED)
        with pytest.raises(ValueError):
            adapter.parse(bad, payload_hash(bad.data))
