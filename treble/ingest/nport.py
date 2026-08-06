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
from treble.core.identifiers import TUID
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
    "curCd",
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
_NULL_IDENTIFIERS: frozenset[str] = frozenset({"", "N/A", "NA", "NONE", "000000000", "0"})


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


def derivative_subject(*, counterparty: str, kind: str, termination: str) -> TUID:
    """Key an OTC derivative by what actually identifies one.

    A swap has no CUSIP, and the previous behaviour keyed it to `cusip:N/A`
    along with every other unidentified holding. What identifies an OTC
    contract is its counterparty and its terms, so that is the key: the
    counterparty, the contract type, and the termination date.

    Raises when the counterparty is unnamed, because a contract with no
    counterparty and no identifier cannot be told apart from another one —
    which is the situation this replaces, not a variation on it.
    """
    party = counterparty.strip().upper()
    if not party or party in _NULL_IDENTIFIERS:
        raise ValueError("derivative holding names no counterparty and has no identifier")
    stamp = termination.strip()[:10] or "open"
    return TUID(f"otc:{party.replace(' ', '_')}:{kind}:{stamp}")


def _text(node: Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


class NportAdapter(SourceAdapter):
    """Fetches N-PORT filings by (CIK, accession) and parses their holdings."""

    meta = SourceMeta(
        source_id="sec-nport",
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
                    subject = _derivative_subject_for(holding)
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


def _derivative_subject_for(holding: Element) -> TUID:
    """The OTC key for a derivative holding, or raise if it is not one."""
    info = holding.find("n:derivativeInfo", NPORT_NS)
    if info is None:
        raise ValueError("not a derivative holding")
    contracts = [child for child in info if child.tag.rsplit("}", 1)[-1].endswith("Deriv")]
    if not contracts:
        raise ValueError("derivative block names no contract type")
    contract = contracts[0]
    return derivative_subject(
        counterparty=_text(contract.find(".//n:counterpartyName", NPORT_NS)),
        kind=contract.tag.rsplit("}", 1)[-1],
        termination=_text(contract.find(".//n:terminationDt", NPORT_NS)),
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
    termination = _text(contract.find(".//n:terminationDt", NPORT_NS))
    if termination not in ("", "N/A"):
        emit("deriv:terminationDt", date.fromisoformat(termination[:10]))
