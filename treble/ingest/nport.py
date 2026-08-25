"""SEC N-PORT holdings adapter (spec §8.1.1, §10.4, §23.1; CLAUDE.md §6).

**Why this adapter matters.** Per-trade corporate bond prints are not
available free: FINRA gates the ``trace`` dataset behind a paid
subscription (probed 2026-07-26 — 404 with a valid individual token), and
the free Gateway lookup's User Agreement forbids automated access. N-PORT
is the legally clean, zero-cost route to *individual corporate bond
valuations*: every registered fund files quarterly holdings, each carrying
CUSIP, ISIN, issuer LEI, par balance, USD fair value, maturity, coupon and
the ASC 820 fair-value level. US public domain, redistributable.

Coverage is quarterly rather than intraday, and marks are the filer's own
valuations rather than executed trades — both stated honestly here because
`TVAL` (§15) weights inputs by exactly this kind of quality metadata, and
``fairValLevel`` is a direct signal of it (Level 1 observed, Level 2
model-with-observable-inputs, Level 3 unobservable).

**Ingest stores what was published.** The implied price per 100 par
(``valUSD / balance * 100``) is deliberately *not* computed here: derived
numbers belong in the analytics layer where the @model decorator stamps
them (I3). This adapter records the two inputs as filed.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any
from xml.etree.ElementTree import Element

import httpx

# Filings arrive over the network: stock ElementTree is vulnerable to
# entity-expansion and external-entity attacks, so parsing goes through
# defusedxml (spec §22.4 supply-chain and input hardening).
from defusedxml.ElementTree import fromstring as safe_fromstring

from treble.core.facts import Fact
from treble.core.identifiers import PLACEHOLDER_IDENTIFIERS, TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.ingest.base import (
    ParsedBatch,
    RawPayload,
    SourceAdapter,
    SourceMeta,
    utcnow,
)
from treble.ingest.edgar import edgar_user_agent
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore

NPORT_NS = {"n": "http://www.sec.gov/edgar/nport"}
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/primary_doc.xml"

# As-filed element names carried straight through (no coined mnemonics).
_HOLDING_TEXT_FIELDS = (
    "name",
    "lei",
    "title",
    "cusip",
    # `curCd` is deliberately NOT here — see `_currency` below. It is one of
    # two mutually exclusive forms and reading only this one silently nulls
    # every foreign-denominated holding.
    "payoffProfile",
    "assetCat",
    "issuerCat",
    "invCountry",
    "isRestrictedSec",
    "fairValLevel",
)
_HOLDING_NUMERIC_FIELDS = ("balance", "valUSD", "pctVal")
_DEBT_TEXT_FIELDS = ("couponKind", "isDefault", "areIntrstPmntsInArrs", "isPaidKind")


#: Identifier values that are filers saying "there isn't one". Treated as
#: absent rather than as identifiers, because they are not unique: every
#: holding filed with `cusip=N/A` keyed to the *same* subject, so unrelated
#: positions from different funds and different filings piled onto one
#: instrument and overwrote each other's fields. The parser's comment said
#: unidentifiable holdings were skipped; this is what makes that true.
#: `000000000` is the same thing written numerically, and appeared on 932
#: facts across 32 fields in the live store.
#: Kept as a module alias for readability; the set itself lives in
#: `core.identifiers` so the entity graph refuses exactly what this does.
_NULL_IDENTIFIERS = PLACEHOLDER_IDENTIFIERS


def _identifier(raw: str) -> str:
    """An identifier, or empty when the filer said there is none."""
    cleaned = raw.strip().upper()
    return "" if cleaned in _NULL_IDENTIFIERS else cleaned


def holding_subject(*, cusip: str, isin: str) -> TUID:
    """Key a holding by its instrument identifier.

    CUSIP/ISIN are stored and matched where they arrive in public filings
    but never bulk-exported (spec §9.3 redistribution guard); FIGI
    resolution happens at security-master population.

    Placeholder identifiers raise rather than keying: `N/A` is not a CUSIP,
    and treating it as one merges every unidentified holding in every filing
    into a single subject whose fields are whatever the last one written
    happened to be.
    """
    if clean_isin := _identifier(isin):
        return TUID(f"isin:{clean_isin}")
    if clean_cusip := _identifier(cusip):
        return TUID(f"cusip:{clean_cusip}")
    raise ValueError("holding has neither CUSIP nor ISIN")


#: Which element carries a contract's own date, by contract type. N-PORT does
#: not use one name for this: only swaps and `othDeriv` have a `terminationDt`,
#: a future expires (`expDate`) and a forward settles (`settlementDt`).
#:
#: Reading `terminationDt` alone — as this did — does not fail loudly. It finds
#: nothing on a future or a forward and stamps the key `open`, so every
#: unexpired future a fund holds against one counterparty keys identically. On
#: the live store that was 51 of 68 derivative holdings, and 45 of them were
#: hidden by the date alone.
_CONTRACT_DATE: dict[str, str] = {
    "futrDeriv": "expDate",
    "fwdDeriv": "settlementDt",
    "swapDeriv": "terminationDt",
    "othDeriv": "terminationDt",
}


def _segment(raw: str) -> str:
    """One segment of a subject key, with the separator taken out.

    A colon inside a segment would make the key ambiguous to anything that
    splits on it, and titles genuinely contain them: `NEXTDC LTD ISSUE 5:27 /
    TERMS 1:1` is a real holding used as a discriminator below.
    """
    return " ".join(raw.split()).upper().replace(":", "-").replace(" ", "_")


def derivative_subject(
    *,
    fund: str,
    counterparty: str,
    kind: str,
    contract_date: str,
    direction: str,
    contract_id: str,
) -> TUID:
    """Key an OTC derivative by what actually identifies one position.

    A swap has no CUSIP, and the behaviour before that keyed it to `cusip:N/A`
    along with every other unidentified holding. Counterparty and terms fixed
    that, but not far enough: `otc:<counterparty>:<kind>:<date>` describes a
    *kind of contract*, not a contract. A fund running fifteen index futures
    with one clearing broker put all fifteen on one subject, and the
    visibility window showed whichever the tie-break ranked first.

    So the key carries the whole of what makes a position distinct:

    * `fund` — an OTC contract is bilateral, not fungible. Two funds short the
      same future against the same broker hold two positions, and keying only
      on the contract would merge them. This is the one segment that has no
      counterpart in `holding_subject`, and the reason is real: an ISIN
      identifies an instrument that many funds may hold, whereas a forward is
      an agreement *this* fund entered into.
    * `counterparty`, `kind`, `contract_date` — the contract's own terms.
    * `direction` — a fund may be long and short the same future at once.
      Measured: dropping this re-merged two positions on the live store.
    * `contract_id` — the filer's own identifier for the contract, from
      `identifiers/other`, which 65 of 68 derivative holdings carry. Where
      there is none the title stands in, which is weaker (a title is prose)
      but is what the filer gave.

    Raises rather than guessing when the counterparty, the fund or the
    discriminator is missing, because a contract that cannot be told apart
    from another one is the situation this replaces, not a variation on it.
    """
    party = counterparty.strip().upper()
    if not party or party in _NULL_IDENTIFIERS:
        raise ValueError("derivative holding names no counterparty and has no identifier")
    if not (scope := _segment(fund)):
        raise ValueError("derivative holding cannot be attributed to a fund")
    if not (contract := _segment(contract_id)):
        raise ValueError("derivative holding carries no identifier and no title")
    stamp = contract_date.strip()[:10] or "open"
    return TUID(
        ":".join(("otc", scope, _segment(party), kind, stamp, _segment(direction) or "-", contract))
    )


def _currency(holding: Element) -> tuple[str | None, float | None]:
    """The holding's denomination, from whichever of the two forms is used.

    N-PORT states currency two mutually exclusive ways. A USD-denominated
    holding carries ``<curCd>USD</curCd>``; a foreign one instead carries
    ``<currencyConditional curCd="CAD" exchangeRt="1.3911"/>`` and has no
    ``curCd`` element at all.

    Reading only the first form nulls the currency of **every non-USD
    holding**, which is precisely the population a permitted-currencies
    mandate rule exists to catch — so the rule came back NOT EVALUABLE on
    the holdings it was written for. Measured on one live filing: 80
    ``<curCd>`` elements against 49 ``currencyConditional``, so a third of
    that fund's positions had no currency in the store.

    Found from the compliance report: 317 equity holdings reported no
    currency, and the raw payload for one of them (a UAE issuer) turned
    out to state it in the attribute form.
    """
    plain = _text(holding.find("n:curCd", NPORT_NS))
    if plain not in ("", "N/A"):
        return plain, None

    conditional = holding.find("n:currencyConditional", NPORT_NS)
    if conditional is None:
        return None, None
    code = (conditional.get("curCd") or "").strip()
    raw_rate = (conditional.get("exchangeRt") or "").strip()
    try:
        rate = float(raw_rate) if raw_rate not in ("", "N/A") else None
    except ValueError:
        # A malformed rate must not cost the currency: the code is the
        # thing a mandate rule reads, and it is right there beside it.
        rate = None
    return (code or None), rate


def _text(node: Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


class NportAdapter(SourceAdapter):
    """Fetches N-PORT filings by (CIK, accession) and parses their holdings."""

    meta = SourceMeta(
        source_id="sec-nport",
        # a per-filing fetch; funds file on their own quarterly cycles.
        expected_cadence_days=None,
        description="SEC N-PORT quarterly fund holdings with per-security fair values",
        licence="US public domain (17 USC 105); freely redistributable",
        redistribution_restricted=False,
        rate_limit_per_second=10.0,
    )
    parser_version = "1"

    def __init__(
        self,
        payloads: PayloadStore,
        log: IngestLog,
        *,
        filings: tuple[tuple[int, str], ...],
        contact_email: str,
    ) -> None:
        super().__init__(payloads, log)
        self._filings = filings
        self._user_agent = edgar_user_agent(contact_email)

    def fetch(self) -> Iterator[RawPayload]:
        headers = {"User-Agent": self._user_agent, "Accept-Encoding": "gzip"}
        with httpx.Client(timeout=90.0, headers=headers) as client:
            for cik, accession in self._filings:
                self._throttle()
                url = ARCHIVE_URL.format(cik=cik, accession=accession.replace("-", ""))
                response = client.get(url)
                response.raise_for_status()
                yield RawPayload(data=response.content, source_uri=url, fetched_at=utcnow())

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        root = safe_fromstring(payload.data)
        holdings = root.findall(".//n:invstOrSec", NPORT_NS)
        if not holdings:
            raise ValueError("not an N-PORT document: no invstOrSec elements")

        # Report period end is the effective date of every mark in the filing.
        period_raw = _text(root.find(".//n:repPdDate", NPORT_NS)) or _text(
            root.find(".//n:repPdEnd", NPORT_NS)
        )
        if not period_raw:
            raise ValueError("N-PORT document has no reporting period date")
        period_end = date.fromisoformat(period_raw)

        # Which fund is filing. Only derivative subjects need it — a holding
        # with an ISIN is keyed by the instrument, which is the same
        # instrument whoever holds it — so a filing that somehow states
        # neither still yields all of its cash positions, and loses only the
        # contracts it cannot attribute. `seriesId` is the fund; `regCik` is
        # the registrant above it, and is the coarser fallback.
        fund = (
            _text(root.find(".//n:seriesId", NPORT_NS))
            or _text(root.find(".//n:regCik", NPORT_NS))
            or _text(root.find(".//n:cik", NPORT_NS))
        )

        provenance = Provenance(
            source_system=self.meta.source_id,
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.XBRL,
            extractor_version=self.parser_version,
            payload_hash=payload_hash,
        )

        facts: list[Fact] = []
        for holding in holdings:
            cusip = _text(holding.find("n:cusip", NPORT_NS))
            isin_node = holding.find("n:identifiers/n:isin", NPORT_NS)
            isin = (isin_node.get("value") or "").strip() if isin_node is not None else ""
            try:
                subject = holding_subject(cusip=cusip, isin=isin)
            except ValueError:
                # No instrument identifier. An OTC derivative legitimately
                # has none, and is keyed by what does identify it — its
                # counterparty and terms. Anything else is genuinely
                # unidentifiable and is skipped, never guessed at.
                try:
                    subject = _derivative_subject_for(holding, fund=fund)
                except ValueError:
                    continue

            # `holding_subject` is bound as a default argument, not captured:
            # a late-binding closure would attribute every fact to the *last*
            # holding if these were ever collected and called after the loop.
            def emit(field: str, value: Any, *, subject: TUID = subject) -> None:
                facts.append(
                    Fact(
                        subject=subject,
                        field=f"nport:{field}",
                        value=value,
                        effective_from=period_end,
                        effective_to=period_end,
                        # No acceptance timestamp inside the document; fetch
                        # time is the provable knowledge bound (I2).
                        knowledge_from=payload.fetched_at,
                        provenance_id=provenance.id,
                    )
                )

            if isin:
                emit("isin", isin)
            for field in _HOLDING_TEXT_FIELDS:
                raw = _text(holding.find(f"n:{field}", NPORT_NS))
                # "N/A" is the filer saying the value is absent, not a value.
                emit(field, None if raw in ("", "N/A") else raw)

            currency, fx_rate = _currency(holding)
            emit("curCd", currency)
            # The filer's own USD conversion rate, carried because valUSD is
            # already converted and a reader reconciling it against `balance`
            # in the local currency otherwise has no way to.
            emit("exchangeRt", fx_rate)
            for field in _HOLDING_NUMERIC_FIELDS:
                raw = _text(holding.find(f"n:{field}", NPORT_NS))
                emit(field, None if raw in ("", "N/A") else float(raw))

            debt = holding.find("n:debtSec", NPORT_NS)
            if debt is not None:
                maturity = _text(debt.find("n:maturityDt", NPORT_NS))
                emit("maturityDt", date.fromisoformat(maturity) if maturity else None)
                rate = _text(debt.find("n:annualizedRt", NPORT_NS))
                emit("annualizedRt", float(rate) if rate not in ("", "N/A") else None)
                for field in _DEBT_TEXT_FIELDS:
                    raw = _text(debt.find(f"n:{field}", NPORT_NS))
                    emit(field, None if raw in ("", "N/A") else raw)

            _emit_derivative(holding, emit)

        return ParsedBatch(provenance=(provenance,), facts=tuple(facts))


#: Counterparty identification on a derivative holding. Ingested because a
#: swap's counterparty *is* half of what the position is: notional against an
#: unnamed counterparty describes market risk and says nothing about who owes
#: it.
_COUNTERPARTY_FIELDS: tuple[str, ...] = ("counterpartyName", "counterpartyLei")

#: Numeric facts on a derivative. `notionalAmt` is deliberately NOT stored
#: under the same field as a cash holding's `valUSD`: a swap's notional is
#: its size, not its worth, and a screen that summed the two would report a
#: book many times larger than it is.
_DERIVATIVE_NUMERIC: tuple[str, ...] = (
    "notionalAmt",
    "unrealizedAppr",
    "upfrontPmnt",
    "upfrontRcpt",
)

#: Text and date facts describing the contract itself.
_DERIVATIVE_TEXT: tuple[str, ...] = (
    "derivCat",
    "payOffProf",
    "swapFlag",
    "pmntCurCd",
    "rcptCurCd",
    "floatingPmntDesc",
    "fixedOrFloating",
)


def _contract_identifier(holding: Element) -> str:
    """The filer's own identifier for a contract, if it gave one.

    Sits beside `isin` under `identifiers`, and was being read past: only
    `isin` was ever looked for there, so a forward carrying
    `<other otherDesc="Forward FX" value="CCTEUR__00343463"/>` looked
    identifierless. It is the closest thing to a contract number N-PORT has.
    """
    other = holding.find("n:identifiers/n:other", NPORT_NS)
    if other is None:
        return ""
    return "" if (raw := (other.get("value") or "").strip()) in _NULL_IDENTIFIERS else raw


def _derivative_subject_for(holding: Element, *, fund: str) -> TUID:
    """The OTC key for a derivative holding, or raise if it is not one."""
    info = holding.find("n:derivativeInfo", NPORT_NS)
    if info is None:
        raise ValueError("not a derivative holding")
    contracts = [child for child in info if child.tag.rsplit("}", 1)[-1].endswith("Deriv")]
    if not contracts:
        raise ValueError("derivative block names no contract type")
    contract = contracts[0]
    kind = contract.tag.rsplit("}", 1)[-1]
    date_element = _CONTRACT_DATE.get(kind, "terminationDt")
    return derivative_subject(
        fund=fund,
        counterparty=_text(contract.find(".//n:counterpartyName", NPORT_NS)),
        kind=kind,
        contract_date=_text(contract.find(f".//n:{date_element}", NPORT_NS)),
        direction=_text(contract.find(".//n:payOffProf", NPORT_NS)),
        # The filer's contract number where there is one, the title where
        # there is not. Never the notional or the mark: those move every
        # quarter, and a key built on them would make one position look like
        # a new one each filing — the opposite failure to the one being fixed.
        contract_id=_contract_identifier(holding) or _text(holding.find("n:title", NPORT_NS)),
    )


def _emit_derivative(holding: Element, emit: Any) -> None:
    """Derivative terms and counterparties, where the holding is one.

    **These were being dropped entirely.** Thirteen holdings per filing in
    the stored payloads carry a `derivativeInfo` block — swaps with named
    counterparties, notionals, unrealised appreciation and termination dates
    — and none of it reached the store. The data was already in the payload
    store; only the parser was not reading it.

    Every field is prefixed `deriv:` so nothing here can be mistaken for a
    cash holding's equivalent. The one that matters most is `notionalAmt`:
    it is the contract's *size*, and a screen that added it to `valUSD`
    across a portfolio would report a book several times larger than it is.
    """
    info = holding.find("n:derivativeInfo", NPORT_NS)
    if info is None:
        return
    # The contract type is the wrapper element's own name — `swapDeriv`,
    # `fwdDeriv`, `futrDeriv`. Read from the tag rather than guessed from
    # which fields happen to be present.
    contracts = [child for child in info if child.tag.rsplit("}", 1)[-1].endswith("Deriv")]
    if not contracts:
        return
    contract = contracts[0]
    emit("deriv:kind", contract.tag.rsplit("}", 1)[-1])

    for party in contract.findall(".//n:counterpartyName/..", NPORT_NS) or [contract]:
        for field in _COUNTERPARTY_FIELDS:
            raw = _text(party.find(f".//n:{field}", NPORT_NS))
            if raw not in ("", "N/A"):
                emit(f"deriv:{field}", raw)
        break  # one counterparty per contract in this schema

    for field in _DERIVATIVE_NUMERIC:
        raw = _text(contract.find(f".//n:{field}", NPORT_NS))
        if raw not in ("", "N/A"):
            emit(f"deriv:{field}", float(raw))
    for field in _DERIVATIVE_TEXT:
        raw = _text(contract.find(f".//n:{field}", NPORT_NS))
        if raw not in ("", "N/A"):
            emit(f"deriv:{field}", raw)
    # Each contract type dates itself differently and only `terminationDt`
    # was being read, so a future's expiry and a forward's settlement date
    # were in the payload and absent from the store. Emitted under their own
    # names rather than flattened into one field: a settlement date is not a
    # termination date, and `_CONTRACT_DATE` already says which to expect.
    for field in ("terminationDt", "expDate", "settlementDt"):
        raw = _text(contract.find(f".//n:{field}", NPORT_NS))
        if raw not in ("", "N/A"):
            emit(f"deriv:{field}", date.fromisoformat(raw[:10]))

    # The identifier the subject is keyed on, stored so the key can be traced
    # back to the filing rather than only reproduced by re-running the parser.
    if identifier := _contract_identifier(holding):
        emit("deriv:contractId", identifier)
