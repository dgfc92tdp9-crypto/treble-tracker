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


def holding_subject(*, cusip: str, isin: str) -> TUID:
    """Key a holding by its instrument identifier.

    CUSIP/ISIN are stored and matched where they arrive in public filings
    but never bulk-exported (spec §9.3 redistribution guard); FIGI
    resolution happens at security-master population.
    """
    if isin:
        return TUID(f"isin:{isin.upper()}")
    if cusip:
        return TUID(f"cusip:{cusip.upper()}")
    raise ValueError("holding has neither CUSIP nor ISIN")


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
            source_system="sec-nport",
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
                # Unidentifiable holdings are skipped, never guessed at.
                continue

            def emit(field: str, value: Any) -> None:
                facts.append(
                    Fact(
                        subject=subject,  # noqa: B023
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

        return ParsedBatch(provenance=(provenance,), facts=tuple(facts))
