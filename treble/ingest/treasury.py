"""US Treasury FiscalData adapter (spec §8.1.1): auction results.

Auction results are the primary golden-value source for bond math
validation (CLAUDE.md §7): each record is a published price/yield pair for
a precisely specified security.

Field naming: values are stored under FiscalData's own published field
names (``int_rate``, ``high_yield``, ``high_price``, …) — as-reported
nomenclature from the source, not coined mnemonics. Mapping onto Treble
field-dictionary mnemonics happens in the field dictionary layer; open
question logged in PROGRESS.md rather than inventing names here
(working agreement: never invent mnemonics).

I2: ``knowledge_from`` is the auction date's end (results are published
auction day) — conservatively, the fetch time if parsing finds no
``auction_date``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
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
    utcnow,
)
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore

AUCTIONS_URL = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
    "/v1/accounting/od/auctions_query"
)

# The as-published numeric fields we extract per auction record.
_NUMERIC_FIELDS = (
    "int_rate",
    "high_yield",
    "high_price",
    "high_discnt_rate",
    "high_investment_rate",
    "bid_to_cover_ratio",
    "offering_amt",
    "total_accepted",
)
_DATE_FIELDS = ("auction_date", "issue_date", "maturity_date", "dated_date")
# `security_type` alone does not distinguish a TIPS: Treasury publishes
# inflation-indexed notes and bonds under the same "Note"/"Bond" types, and
# only this flag separates them. Without it a TIPS is indistinguishable from
# a nominal bond in the store, and pricing one as nominal yields a
# confidently-displayed wrong number — a 5-Year TIPS in this dataset prices
# to a 1.32% real yield that would read as a nominal yield beside 4% notes.
#
# `original_security_term` is carried because a reopening reports its
# remaining term ("9-Year 10-Month") while the instrument is a 10-year note.
_TEXT_FIELDS = (
    "security_type",
    "security_term",
    "original_security_term",
    "inflation_index_security",
)


def cusip_subject(cusip: str) -> TUID:
    """Deterministic subject key for replay stability (I5). CUSIP is stored
    and matched where it arrives in public data but never bulk-exported
    (spec §9.3 redistribution guard)."""
    return TUID(f"cusip:{cusip.upper()}")


class TreasuryAuctionsAdapter(SourceAdapter):
    meta = SourceMeta(
        source_id="treasury-auctions",
        # auctions run on a weekly cycle across the bill and note calendar.
        expected_cadence_days=7.0,
        description="US Treasury FiscalData auction results",
        licence="US public domain (17 USC 105)",
        redistribution_restricted=False,
        rate_limit_per_second=2.0,
    )
    parser_version = "1"

    def __init__(
        self,
        payloads: PayloadStore,
        log: IngestLog,
        *,
        since: date,
        page_size: int = 500,
    ) -> None:
        super().__init__(payloads, log)
        self._since = since
        self._page_size = page_size

    def fetch(self) -> Iterator[RawPayload]:
        page = 1
        with httpx.Client(timeout=60.0) as client:
            while True:
                self._throttle()
                url = (
                    f"{AUCTIONS_URL}?filter=auction_date:gte:{self._since.isoformat()}"
                    f"&page[size]={self._page_size}&page[number]={page}"
                )
                response = client.get(url)
                response.raise_for_status()
                yield RawPayload(data=response.content, source_uri=url, fetched_at=utcnow())
                body = response.json()
                total_pages = int(body.get("meta", {}).get("total-pages", 1))
                if page >= total_pages:
                    return
                page += 1

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        body: dict[str, Any] = json.loads(payload.data)
        provenance = Provenance(
            source_system=self.meta.source_id,
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.API,
            extractor_version=self.parser_version,
            payload_hash=payload_hash,
        )
        facts: list[Fact] = []
        for record in body.get("data", []):
            cusip = record.get("cusip")
            auction_day_raw = record.get("auction_date")
            if not cusip or not auction_day_raw:
                continue
            auction_day = date.fromisoformat(auction_day_raw)
            # Results are published on auction day; end-of-day is the
            # conservative knowledge time (I2).
            knowledge = datetime.combine(auction_day, time(23, 59), tzinfo=UTC)
            if knowledge > payload.fetched_at:
                knowledge = payload.fetched_at
            subject = cusip_subject(cusip)

            # Per-row values are bound as default arguments, not captured: a
            # late-binding closure would stamp every fact with the *last*
            # auction's CUSIP and dates if these were called after the loop.
            def emit(
                field: str,
                value: float | str | date | None,
                *,
                subject: TUID = subject,
                auction_day: date = auction_day,
                knowledge: datetime = knowledge,
            ) -> None:
                facts.append(
                    Fact(
                        subject=subject,
                        field=field,
                        value=value,
                        effective_from=auction_day,
                        effective_to=auction_day,
                        knowledge_from=knowledge,
                        provenance_id=provenance.id,
                    )
                )

            for field in _NUMERIC_FIELDS:
                raw = record.get(field)
                emit(field, None if raw in (None, "null", "") else float(raw))
            for field in _DATE_FIELDS:
                raw = record.get(field)
                emit(field, None if raw in (None, "null", "") else date.fromisoformat(raw))
            for field in _TEXT_FIELDS:
                raw = record.get(field)
                emit(field, None if raw in (None, "null", "") else str(raw))
        return ParsedBatch(provenance=(provenance,), facts=tuple(facts))
