"""SEC EDGAR adapters (spec §8.1.1; CLAUDE.md §6).

Two adapters share one token bucket (EDGAR's 10 req/s limit is per
requester, not per endpoint) and one mandatory identifying User-Agent —
fetching without a contact email is refused, not defaulted.

**EdgarCompanyFactsAdapter** — XBRL company facts. Every row in the payload
is a (tag, unit, period, value, filing) tuple: repeated reports of the same
period across filings are *separate knowledge events*, which is exactly the
bitemporal model (I2) — later filings supersede at read time, and history is
never rewritten.

I2 note on knowledge dates: companyfacts rows carry the ``filed`` date (no
timestamp). ``knowledge_from`` here is filed-date end-of-day UTC — a
*conservative upper bound* (EDGAR acceptance always precedes it). The exact
``acceptanceDateTime`` per accession comes from the submissions adapter and
tightens these at security-master population; conservative-late is the safe
direction for backtests (never claims knowledge earlier than true).

**EdgarSubmissionsAdapter** — filing index with acceptance timestamps; also
exposes :func:`accepted_times` for the knowledge-date join above.

Bulk note (ADR-0005): the all-filers universe uses the nightly bulk archives
(``companyfacts.zip``/``submissions.zip``) through the same parsers — one
JSON document per company either way. Crawling per-company at scale is slow
and rude; the per-company fetch exists for incremental refresh.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime, time
from typing import Any

import httpx

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.ingest.base import (
    ParsedBatch,
    RawPayload,
    SourceAdapter,
    SourceMeta,
    TokenBucket,
    utcnow,
)
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:0>10}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:0>10}.json"

# One bucket for every EDGAR endpoint in this process (CLAUDE.md §6).
_EDGAR_BUCKET = TokenBucket(rate_per_second=10.0, burst=10)


class MissingContactError(Exception):
    """EDGAR requires an identifying User-Agent with a contact email."""


def edgar_user_agent(contact_email: str) -> str:
    if "@" not in contact_email:
        raise MissingContactError(
            "EDGAR requests need a real contact email in the User-Agent; "
            "set TREBLE_EDGAR_CONTACT or pass contact_email"
        )
    return f"TrebleTracker/0.1 ({contact_email})"


def cik_subject(cik: int | str) -> TUID:
    return TUID(f"cik:{int(cik):010d}")


def _filed_eod_utc(filed: str) -> datetime:
    return datetime.combine(date.fromisoformat(filed), time(23, 59, 59), tzinfo=UTC)


class EdgarCompanyFactsAdapter(SourceAdapter):
    meta = SourceMeta(
        source_id="edgar-companyfacts",
        description="SEC EDGAR XBRL company facts API / bulk archive",
        licence="US public domain (SEC EDGAR dissemination)",
        redistribution_restricted=False,
        rate_limit_per_second=10.0,
    )
    parser_version = "1"

    def __init__(
        self,
        payloads: PayloadStore,
        log: IngestLog,
        *,
        ciks: tuple[int, ...],
        contact_email: str,
        accepted: Mapping[str, datetime] | None = None,
    ) -> None:
        super().__init__(payloads, log)
        self._ciks = ciks
        self._user_agent = edgar_user_agent(contact_email)
        #: accession -> acceptance time, from the submissions payload.
        #: companyfacts states only a filing *date*, so without this every
        #: filing made on one day shares a knowledge instant and cannot be
        #: ordered. Apple filed two documents on 2015-01-28 reporting
        #: 4,033,000,000 and 4,000,000,000 for the same period; collapsed to
        #: end-of-day they became an unresolvable pair rather than a
        #: restatement. Optional so the adapter still works before
        #: submissions are ingested, falling back to the coarser date.
        self._accepted: Mapping[str, datetime] = accepted or {}

    def fetch(self) -> Iterator[RawPayload]:
        headers = {"User-Agent": self._user_agent, "Accept-Encoding": "gzip"}
        with httpx.Client(timeout=60.0, headers=headers) as client:
            for cik in self._ciks:
                _EDGAR_BUCKET.acquire()
                url = COMPANYFACTS_URL.format(cik=cik)
                response = client.get(url)
                response.raise_for_status()
                yield RawPayload(data=response.content, source_uri=url, fetched_at=utcnow())

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        doc: dict[str, Any] = json.loads(payload.data)
        cik = doc.get("cik")
        if cik is None:
            raise ValueError("not a companyfacts document: no cik")
        subject = cik_subject(cik)
        provenance = Provenance(
            source_system="edgar",
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.XBRL,
            extractor_version=self.parser_version,
            payload_hash=payload_hash,
        )
        facts: list[Fact] = []
        for taxonomy, tags in doc.get("facts", {}).items():
            for tag, body in tags.items():
                for unit, rows in body.get("units", {}).items():
                    field = f"{taxonomy}:{tag}:{unit}"
                    for row in rows:
                        end = row.get("end")
                        filed = row.get("filed")
                        value = row.get("val")
                        if end is None or filed is None or value is None:
                            continue
                        start = row.get("start")
                        # The row's own accession resolves when this figure
                        # became public. Falling back to end-of-day keeps the
                        # old behaviour when submissions have not been
                        # ingested, at the old resolution.
                        accession = row.get("accn")
                        known = self._accepted.get(accession) if accession else None
                        facts.append(
                            Fact(
                                subject=subject,
                                field=field,
                                value=float(value)
                                if isinstance(value, int | float)
                                else str(value),
                                effective_from=date.fromisoformat(start or end),
                                effective_to=date.fromisoformat(end),
                                knowledge_from=known or _filed_eod_utc(filed),
                                provenance_id=provenance.id,
                            )
                        )
        return ParsedBatch(provenance=(provenance,), facts=tuple(facts))


class EdgarSubmissionsAdapter(SourceAdapter):
    meta = SourceMeta(
        source_id="edgar-submissions",
        description="SEC EDGAR submissions index (acceptance timestamps)",
        licence="US public domain (SEC EDGAR dissemination)",
        redistribution_restricted=False,
        rate_limit_per_second=10.0,
    )
    parser_version = "1"

    def __init__(
        self,
        payloads: PayloadStore,
        log: IngestLog,
        *,
        ciks: tuple[int, ...],
        contact_email: str,
    ) -> None:
        super().__init__(payloads, log)
        self._ciks = ciks
        self._user_agent = edgar_user_agent(contact_email)

    def fetch(self) -> Iterator[RawPayload]:
        headers = {"User-Agent": self._user_agent, "Accept-Encoding": "gzip"}
        with httpx.Client(timeout=60.0, headers=headers) as client:
            for cik in self._ciks:
                _EDGAR_BUCKET.acquire()
                url = SUBMISSIONS_URL.format(cik=cik)
                response = client.get(url)
                response.raise_for_status()
                for name in submission_pages(response.content):
                    # Older filings live here; without them the knowledge
                    # date for anything beyond the most recent 1000 filings
                    # falls back to end-of-day.
                    _EDGAR_BUCKET.acquire()
                    page_url = SUBMISSIONS_PAGE_URL.format(name=name)
                    page = client.get(page_url)
                    page.raise_for_status()
                    yield RawPayload(data=page.content, source_uri=page_url, fetched_at=utcnow())
                yield RawPayload(data=response.content, source_uri=url, fetched_at=utcnow())

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        doc: dict[str, Any] = json.loads(payload.data)
        # Two shapes: the main document, and the older pages it points to.
        # A page carries the same arrays bare — no `filings` wrapper and no
        # `cik` — so the filer is read from the URI it was fetched from.
        # Parsing pages as well means filings older than the most recent
        # 1000 are recorded rather than merely stored.
        recent = doc.get("filings", {}).get("recent")
        cik = doc.get("cik")
        if recent is None and "accessionNumber" in doc:
            recent = doc
            cik = _cik_from_page_uri(payload.source_uri)
        if cik is None or recent is None:
            raise ValueError("not a submissions document")
        subject = cik_subject(cik)
        provenance = Provenance(
            source_system="edgar",
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.API,
            extractor_version=self.parser_version,
            payload_hash=payload_hash,
        )
        facts: list[Fact] = []
        for accession, form, filing_date, accepted in zip(
            recent.get("accessionNumber", []),
            recent.get("form", []),
            recent.get("filingDate", []),
            recent.get("acceptanceDateTime", []),
            strict=True,
        ):
            accepted_at = datetime.fromisoformat(accepted.replace("Z", "+00:00"))
            facts.append(
                Fact(
                    subject=subject,
                    field="edgar:filing:form",
                    value=str(form),
                    effective_from=date.fromisoformat(filing_date),
                    effective_to=date.fromisoformat(filing_date),
                    knowledge_from=accepted_at,
                    provenance_id=provenance.id,
                )
            )
            # The accession is recoverable from the locator convention below
            # via accepted_times(); no invented field mnemonics needed.
            _ = accession
        return ParsedBatch(provenance=(provenance,), facts=tuple(facts))


SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"


def submission_pages(submissions_payload: bytes) -> tuple[str, ...]:
    """Names of the older submission files this document points to.

    ``filings.recent`` holds at most 1000 filings. Apple's stops at
    2015-05-29; everything before that — 1,236 filings back to 1994 — is in
    a separate page. Without fetching those, acceptance times exist only for
    recent filings and older ones keep filing-date resolution, which is
    exactly what left eleven same-day conflicts unresolvable.
    """
    doc = json.loads(submissions_payload)
    files = doc.get("filings", {}).get("files", []) or []
    return tuple(entry["name"] for entry in files if entry.get("name"))


def _cik_from_page_uri(source_uri: str) -> int | None:
    """The filer a submissions page belongs to, from its filename.

    Pages are named `CIK0000320193-submissions-001.json` and carry no `cik`
    field of their own.
    """
    match = re.search(r"CIK(\d{10})", source_uri)
    return int(match.group(1)) if match else None


def accepted_times(submissions_payload: bytes) -> dict[str, datetime]:
    """accession number -> acceptanceDateTime (UTC), for the I2 knowledge-date
    join at security-master population. Pure function of the payload.

    Accepts either the main submissions document or one of its older pages;
    a page carries the same arrays without the ``filings`` wrapper.
    """
    doc = json.loads(submissions_payload)
    recent = doc.get("filings", {}).get("recent") or (doc if "accessionNumber" in doc else {})
    out: dict[str, datetime] = {}
    for accession, accepted in zip(
        recent.get("accessionNumber", []),
        recent.get("acceptanceDateTime", []),
        strict=True,
    ):
        out[accession] = datetime.fromisoformat(accepted.replace("Z", "+00:00"))
    return out
