"""N-PORT adapter against a real recorded filing (no network).

Fixture: primary_doc.xml from accession 0002000324-26-002035 (CIK 1484018),
recorded 2026-07-26. Assertions read expected values out of the XML itself,
so the test verifies the parse rather than hand-typed numbers.
"""

import re
from datetime import UTC, date, datetime
from pathlib import Path
from xml.etree.ElementTree import Element

import pytest

from treble.core.identifiers import parse_position_subject, validate_lei
from treble.ingest.base import RawPayload
from treble.ingest.nport import NportAdapter, _currency, holding_subject
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash

FIXTURE = Path(__file__).parent.parent / "fixtures" / "nport" / "nport_sample.xml"
FETCHED = datetime(2026, 7, 26, 18, 0, tzinfo=UTC)
CONTACT = "test@example.com"


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


def _instrument_with_position(facts) -> dict[object, dict[str, object]]:  # type: ignore[no-untyped-def]
    """Each instrument's fields with its own fund's position folded in.

    The join every reader now has to do: an instrument subject carries what
    the bond is, a position subject carries what one fund holds of it.
    """
    merged: dict[object, dict[str, object]] = {}
    for fact in facts:
        parsed = parse_position_subject(fact.subject)
        key = parsed[1] if parsed else fact.subject
        merged.setdefault(key, {})[fact.field] = fact.value
    return merged


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
        by_field = _instrument_with_position(batch.facts)[subject]
        assert by_field["nport:valUSD"] == pytest.approx(float(tag("valUSD")))
        assert by_field["nport:balance"] == pytest.approx(float(tag("balance")))
        assert by_field["nport:issuerCat"] == "CORP"
        assert by_field["nport:assetCat"] == "DBT"
        # ASC 820 level drives TVAL input quality (spec §15.2).
        assert by_field["nport:fairValLevel"] in {"1", "2", "3"}
        # Debt terms come through for the analytics layer.
        assert isinstance(by_field["nport:maturityDt"], date)
        assert by_field["nport:annualizedRt"] == pytest.approx(float(tag("annualizedRt")))

    def test_the_instrument_and_the_position_are_separate_subjects(
        self, adapter: NportAdapter
    ) -> None:
        """What the bond is, and how much of it one fund owns, are different
        questions. Keying both to `isin:` meant three funds' marks landed in
        one partition and the window showed whichever was fetched last."""
        raw = payload()
        batch = adapter.parse(raw, payload_hash(raw.data))
        on_instrument = {f.field for f in batch.facts if str(f.subject).startswith("isin:")}
        on_position = {f.field for f in batch.facts if str(f.subject).startswith("pos:")}
        # The fund's own numbers are never on the shared instrument subject.
        assert not on_instrument & {"nport:valUSD", "nport:balance", "nport:pctVal"}
        assert {"nport:valUSD", "nport:balance", "nport:pctVal"} <= on_position
        # The instrument's own facts are never duplicated onto the position.
        assert not on_position & {"nport:maturityDt", "nport:issuerCat", "nport:assetCat"}

    def test_every_position_names_its_fund(self, adapter: NportAdapter) -> None:
        raw = payload()
        batch = adapter.parse(raw, payload_hash(raw.data))
        positions = {str(f.subject) for f in batch.facts if str(f.subject).startswith("pos:")}
        assert positions, "no positions emitted"
        for subject in positions:
            parsed = parse_position_subject(subject)
            assert parsed is not None, subject
            fund, instrument = parsed
            assert fund and str(instrument).startswith(("isin:", "cusip:"))

    def test_implied_price_is_computable_and_sane(self, adapter: NportAdapter) -> None:
        """valUSD / balance * 100 must land in a plausible bond-price range —
        the whole point of this source. Ingest stores the inputs; this asserts
        they are consistent enough for the analytics layer to divide.

        Joins the position back to its instrument, because the price needs
        `assetCat` from one and the two numbers from the other."""
        raw = payload()
        batch = adapter.parse(raw, payload_hash(raw.data))
        priced = 0
        for fields in _instrument_with_position(batch.facts).values():
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


class TestPlaceholderIdentifiersAreNotIdentifiers:
    """A defect found on 2026-08-06, and it had been there all along.

    N-PORT filers write `cusip=N/A` for holdings with no CUSIP — chiefly OTC
    derivatives. `holding_subject` accepted that literal string, so **every
    unidentified holding in every filing keyed to the same subject**:
    `cusip:N/A` carried 2,110 facts across 26 fields on the live store, each
    position overwriting the last. The parser's comment said unidentifiable
    holdings were skipped; the guard had never fired.
    """

    @pytest.mark.parametrize("placeholder", ["N/A", "n/a", " N/A ", "000000000", "0", "", "NONE"])
    def test_a_placeholder_cusip_is_refused(self, placeholder: str) -> None:
        with pytest.raises(ValueError, match="neither CUSIP nor ISIN"):
            holding_subject(cusip=placeholder, isin="")

    @pytest.mark.parametrize("placeholder", ["N/A", "000000000", ""])
    def test_a_placeholder_isin_is_refused(self, placeholder: str) -> None:
        with pytest.raises(ValueError, match="neither CUSIP nor ISIN"):
            holding_subject(cusip="", isin=placeholder)

    def test_a_real_identifier_still_keys(self) -> None:
        """The guard must not have made the ordinary case stricter."""
        assert holding_subject(cusip="912810UT3", isin="") == "cusip:912810UT3"
        assert holding_subject(cusip="912810UT3", isin="US912810UT36") == "isin:US912810UT36"

    def test_no_holding_lands_on_a_placeholder_subject(self, adapter: NportAdapter) -> None:
        """The end-to-end form: nothing may be keyed to a subject whose
        identifier is a filer's way of saying there isn't one."""
        raw = payload()
        facts = adapter.parse(raw, payload_hash(raw.data)).facts
        keys = {str(fact.subject).split(":", 1)[1].upper() for fact in facts}
        assert not keys & {"N/A", "000000000", "", "NONE"}


#: A minimal filing carrying one OTC swap. Synthetic and obviously so: the
#: recorded fixture holds no derivatives, and editing a recorded payload to
#: add one would make it no longer a recording.
_SWAP_DOC = """<?xml version="1.0"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData><genInfo>
    <regCik>0001593547</regCik><seriesId>S000052180</seriesId>
    <repPdDate>2026-03-31</repPdDate>
  </genInfo>
  <invstOrSecs>
    <invstOrSec>
      <name>N/A</name><cusip>N/A</cusip>
      <title>USD SOFR 10Y RECEIVE FIXED</title>
      <identifiers><other otherDesc="Internal Identifier" value="SWP-000431"/></identifiers>
      <valUSD>1000.00</valUSD><pctVal>0.01</pctVal><balance>0</balance>
      <derivativeInfo><swapDeriv>
        <counterparties>
          <counterpartyName>BANK OF AMERICA, N.A.</counterpartyName>
          <counterpartyLei>B4TYDEB6GKMZO031MB27</counterpartyLei>
        </counterparties>
        <payOffProf>Long</payOffProf>
        <notionalAmt>250000000.00</notionalAmt>
        <unrealizedAppr>-1234567.00</unrealizedAppr>
        <terminationDt>2031-06-20</terminationDt>
      </swapDeriv></derivativeInfo>
    </invstOrSec>
  </invstOrSecs></formData>
</edgarSubmission>
"""

#: Two index futures against one clearing broker, as a filer states them: no
#: `terminationDt` anywhere, because a future expires rather than terminating.
#: Reading only `terminationDt` stamps both `open` and keys them identically.
_FUTURES_DOC = """<?xml version="1.0"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData><genInfo>
    <regCik>0001593547</regCik><seriesId>S000052180</seriesId>
    <repPdDate>2026-04-30</repPdDate>
  </genInfo>
  <invstOrSecs>
    <invstOrSec>
      <name>N/A</name><cusip>N/A</cusip>
      <title>S&amp;P500 EMINI FUT JUN26</title>
      <identifiers><other otherDesc="Bloomberg Ticker" value="ESM6"/></identifiers>
      <valUSD>1918571.90</valUSD><pctVal>1.45</pctVal><balance>15</balance>
      <derivativeInfo><futrDeriv derivCat="FUT">
        <counterparties>
          <counterpartyName>MORGAN STANLEY &amp; CO, INC</counterpartyName>
        </counterparties>
        <payOffProf>Long</payOffProf>
        <expDate>2026-06-19</expDate>
        <notionalAmt>2160550.35</notionalAmt>
      </futrDeriv></derivativeInfo>
    </invstOrSec>
    <invstOrSec>
      <name>N/A</name><cusip>N/A</cusip>
      <title>TOPIX INDX FUTR JUN26</title>
      <identifiers><other otherDesc="Bloomberg Ticker" value="TPM6"/></identifiers>
      <valUSD>-1979965.00</valUSD><pctVal>-3.58</pctVal><balance>-48</balance>
      <derivativeInfo><futrDeriv derivCat="FUT">
        <counterparties>
          <counterpartyName>MORGAN STANLEY &amp; CO, INC</counterpartyName>
        </counterparties>
        <payOffProf>Short</payOffProf>
        <expDate>2026-06-12</expDate>
        <notionalAmt>-3603924.09</notionalAmt>
      </futrDeriv></derivativeInfo>
    </invstOrSec>
  </invstOrSecs></formData>
</edgarSubmission>
"""


#: A representative contract, as the arguments the subject is built from.
#: Written once because every key test varies one segment of it.
_CONTRACT: dict[str, str] = {
    "fund": "S000052180",
    "counterparty": "BANK OF AMERICA, N.A.",
    "kind": "swapDeriv",
    "contract_date": "2031-06-20",
    "direction": "Long",
    "contract_id": "SWP-000431",
}

#: `otc:fund:counterparty:kind:date:direction:contract` — the separator count
#: is asserted so a segment cannot be forged by punctuation inside a value.
_CONTRACT_SEGMENTS = 6


def _facts_from(adapter: NportAdapter, document: str):  # type: ignore[no-untyped-def]
    data = document.encode()
    raw = RawPayload(data=data, source_uri="https://example.invalid/d", fetched_at=FETCHED)
    return adapter.parse(raw, payload_hash(data)).facts


def _swap_facts(adapter: NportAdapter):  # type: ignore[no-untyped-def]
    return _facts_from(adapter, _SWAP_DOC)


class TestDerivativesAreKeyedByWhatIdentifiesThem:
    """An OTC swap has no CUSIP. What identifies it is its counterparty and
    terms, so that is its subject — rather than the shared `cusip:N/A` every
    such holding used to collapse onto."""

    def test_a_swap_is_kept_rather_than_skipped(self, adapter: NportAdapter) -> None:
        facts = _swap_facts(adapter)
        assert facts, "the swap was dropped entirely"
        assert all(str(f.subject).startswith("otc:") for f in facts)

    def test_the_counterparty_and_terms_are_recorded(self, adapter: NportAdapter) -> None:
        """Notional against an unnamed counterparty describes market risk and
        says nothing about who owes it."""
        values = {f.field: f.value for f in _swap_facts(adapter)}
        assert values["nport:deriv:counterpartyName"] == "BANK OF AMERICA, N.A."
        assert values["nport:deriv:counterpartyLei"] == "B4TYDEB6GKMZO031MB27"
        assert values["nport:deriv:notionalAmt"] == pytest.approx(250_000_000.0)
        assert values["nport:deriv:unrealizedAppr"] == pytest.approx(-1_234_567.0)
        assert values["nport:deriv:terminationDt"] == date(2031, 6, 20)
        assert values["nport:deriv:kind"] == "swapDeriv"

    def test_notional_is_not_stored_as_a_value(self, adapter: NportAdapter) -> None:
        """A swap's notional is its size, not its worth. A screen summing
        `notionalAmt` with `valUSD` across a book would report it several
        times larger, and every number in that sum would be one the filer
        actually reported."""
        values = {f.field: f.value for f in _swap_facts(adapter)}
        assert values["nport:deriv:notionalAmt"] != values.get("nport:valUSD")
        assert values.get("nport:valUSD") == pytest.approx(1000.0)

    def test_two_counterparties_do_not_collapse(self) -> None:
        from treble.ingest.nport import derivative_subject

        first = derivative_subject(**{**_CONTRACT, "counterparty": "BANK OF AMERICA, N.A."})
        second = derivative_subject(**{**_CONTRACT, "counterparty": "GOLDMAN SACHS INTERNATIONAL"})
        assert first != second
        assert first.startswith("otc:")

    def test_the_same_contract_keys_the_same_way(self) -> None:
        """Stable across filings, or a fund's position would look like a new
        contract every quarter."""
        from treble.ingest.nport import derivative_subject

        assert derivative_subject(**_CONTRACT) == derivative_subject(**_CONTRACT)

    def test_an_unnamed_counterparty_is_refused(self) -> None:
        """This is the situation being replaced, not a variation on it: a
        contract with no counterparty and no identifier cannot be told apart
        from another one."""
        from treble.ingest.nport import derivative_subject

        for name in ("", "N/A", "   "):
            with pytest.raises(ValueError, match="names no counterparty"):
                derivative_subject(**{**_CONTRACT, "counterparty": name})

    def test_an_open_ended_contract_still_keys(self) -> None:
        from treble.ingest.nport import derivative_subject

        assert ":open:" in derivative_subject(**{**_CONTRACT, "contract_date": ""})

    def test_one_broker_many_futures_do_not_collapse(self) -> None:
        """The defect this key replaces. Fifteen index futures against one
        clearing broker, none of them carrying a termination date, keyed
        identically — `otc:MORGAN_STANLEY_&_CO,_INC:futrDeriv:open` held 15
        distinct notionals on the live store and a screen could show one."""
        from treble.ingest.nport import derivative_subject

        book = [
            {
                **_CONTRACT,
                "kind": "futrDeriv",
                "contract_date": "2026-06-19",
                "contract_id": ticker,
                "direction": side,
            }
            for ticker in ("ESM6", "NQM6", "TPM6")
            for side in ("Long", "Short")
        ]
        assert len({derivative_subject(**c) for c in book}) == len(book)

    def test_two_funds_holding_one_contract_stay_apart(self) -> None:
        """An ISIN names an instrument many funds may hold; a forward is an
        agreement one fund entered into. Keying without the fund merges two
        funds' positions into one, which is the same defect one level up."""
        from treble.ingest.nport import derivative_subject

        assert derivative_subject(**{**_CONTRACT, "fund": "S000002839"}) != derivative_subject(
            **{**_CONTRACT, "fund": "S000052180"}
        )

    def test_a_colon_in_the_discriminator_cannot_forge_a_segment(self) -> None:
        """`NEXTDC LTD ISSUE 5:27 / TERMS 1:1` is a real holding, and the
        title is what keys a contract the filer gave no identifier for."""
        from treble.ingest.nport import derivative_subject

        subject = derivative_subject(**{**_CONTRACT, "contract_id": "ISSUE 5:27 / TERMS 1:1"})
        assert subject.count(":") == _CONTRACT_SEGMENTS
        assert "ISSUE_5-27_/_TERMS_1-1" in subject

    def test_an_unidentifiable_contract_is_refused(self) -> None:
        from treble.ingest.nport import derivative_subject

        with pytest.raises(ValueError, match="no identifier and no title"):
            derivative_subject(**{**_CONTRACT, "contract_id": ""})
        with pytest.raises(ValueError, match="cannot be attributed to a fund"):
            derivative_subject(**{**_CONTRACT, "fund": ""})


class TestEachContractTypeDatesItselfDifferently:
    """Only swaps carry a `terminationDt`. Reading that one element for every
    contract type finds nothing on a future or a forward and stamps the key
    `open`, which is not a parse failure — it is a key that silently merges
    every unexpired contract a fund holds against one broker."""

    def test_a_future_is_keyed_by_its_expiry_not_open(self, adapter: NportAdapter) -> None:
        facts = _facts_from(adapter, _FUTURES_DOC)
        subjects = {str(f.subject) for f in facts}
        assert not any(":open:" in s for s in subjects), subjects
        assert any("2026-06-19" in s for s in subjects)

    def test_two_futures_on_one_broker_stay_apart(self, adapter: NportAdapter) -> None:
        """The live defect: 15 index futures against Morgan Stanley on one
        subject, 13 distinct titles invisible behind the tie-break."""
        subjects = {str(f.subject) for f in _facts_from(adapter, _FUTURES_DOC)}
        assert len(subjects) == 2, subjects

    def test_the_expiry_reaches_the_store(self, adapter: NportAdapter) -> None:
        """It was in the payload and in no fact: only `terminationDt` was
        emitted, so a future's expiry was parsed for the key and dropped."""
        values = {(str(f.subject), f.field): f.value for f in _facts_from(adapter, _FUTURES_DOC)}
        expiries = {v for (_, field), v in values.items() if field == "nport:deriv:expDate"}
        assert expiries == {date(2026, 6, 19), date(2026, 6, 12)}

    def test_the_keying_identifier_is_stored(self, adapter: NportAdapter) -> None:
        ids = {
            f.value
            for f in _facts_from(adapter, _FUTURES_DOC)
            if f.field == "nport:deriv:contractId"
        }
        assert ids == {"ESM6", "TPM6"}


class TestBothCurrencyForms:
    """N-PORT states currency two mutually exclusive ways, and reading
    only one nulls every foreign-denominated holding.

    Measured on a live filing: 80 `<curCd>` elements against 49
    `currencyConditional`, so a third of that fund's positions had no
    currency in the store — and `permitted_currencies` came back NOT
    EVALUABLE on exactly the population it exists to catch.
    """

    @staticmethod
    def _holding(inner: str) -> Element:
        from defusedxml.ElementTree import fromstring

        return fromstring(
            f'<invstOrSec xmlns="http://www.sec.gov/edgar/nport"><name>X</name>{inner}</invstOrSec>'
        )

    def test_the_plain_element_form(self) -> None:
        currency, rate = _currency(self._holding("<curCd>USD</curCd>"))
        assert (currency, rate) == ("USD", None)

    def test_the_conditional_attribute_form(self) -> None:
        currency, rate = _currency(
            self._holding('<currencyConditional curCd="CAD" exchangeRt="1.391100"/>')
        )
        assert currency == "CAD"
        assert rate == pytest.approx(1.3911)

    def test_neither_form_is_a_null_not_an_error(self) -> None:
        assert _currency(self._holding("")) == (None, None)

    def test_a_malformed_rate_does_not_cost_the_currency(self) -> None:
        """The code is what a mandate rule reads, and it is right there
        beside the rate that failed to parse."""
        currency, rate = _currency(
            self._holding('<currencyConditional curCd="JPY" exchangeRt="n/a"/>')
        )
        assert (currency, rate) == ("JPY", None)

    def test_na_in_the_plain_form_falls_through_to_the_conditional(self) -> None:
        """A filer writing N/A in one form and the real value in the other
        must not have the N/A win."""
        currency, _ = _currency(
            self._holding('<curCd>N/A</curCd><currencyConditional curCd="EUR"/>')
        )
        assert currency == "EUR"
