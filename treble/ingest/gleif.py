"""GLEIF LEI adapters (spec §9.5; CLAUDE.md §6).

LEI is the primary *entity* key. ``GleifAdapter`` handles record-level API
responses (JSON:API) for individual-entity lookups.

``GleifRelationshipAdapter`` builds the parent/subsidiary and fund entity
graph (§9.5) from GLEIF's Level 2 Relationship Record bulk concatenated
file (RR-CDF 2.1) — per CLAUDE.md, the bulk file, not the per-record API,
is the source for the graph at all-filers scale. Schema verified against a
live-downloaded file (2026-07-27, 660,674 records): every ``StartNode`` /
``EndNode`` carries ``NodeIDType`` LEI with no exceptions, so an
unrecognised node id type is treated as a schema change and raises rather
than being silently skipped or guessed at.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from datetime import date
from typing import Any
from xml.etree.ElementTree import Element

import httpx

# Bulk files arrive over the network: stock ElementTree is vulnerable to
# entity-expansion and external-entity attacks, so parsing goes through
# defusedxml (spec §22.4 supply-chain and input hardening) — same as N-PORT.
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
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore

RECORDS_URL = "https://api.gleif.org/api/v1/lei-records"

# Discovers the current publish id, then downloads it — the id in the
# download URL increments with every publish (observed daily), so it
# cannot be hardcoded (CLAUDE.md: bulk-preferred, no guessed endpoints).
CONCATENATED_META_URL = "https://leidata.gleif.org/api/v1/concatenated-files/rr"
CONCATENATED_DOWNLOAD_URL = (
    "https://leidata.gleif.org/api/v1/concatenated-files/rr/get/{file_id}/zip"
)

RR_NS = {"rr": "http://www.gleif.org/data/schema/rr/2016"}


def lei_subject(lei: str) -> TUID:
    return TUID(f"lei:{lei.upper()}")


class GleifAdapter(SourceAdapter):
    meta = SourceMeta(
        source_id="gleif",
        # a per-LEI lookup, not a feed.
        expected_cadence_days=None,
        description="GLEIF LEI records API (bulk concatenated files at scale)",
        licence="CC0 — GLEIF data is fully open",
        redistribution_restricted=False,
        rate_limit_per_second=1.0,
    )
    parser_version = "1"

    def __init__(
        self,
        payloads: PayloadStore,
        log: IngestLog,
        *,
        leis: tuple[str, ...] = (),
        legal_names: tuple[str, ...] = (),
    ) -> None:
        super().__init__(payloads, log)
        self._leis = leis
        self._legal_names = legal_names

    def fetch(self) -> Iterator[RawPayload]:
        with httpx.Client(timeout=60.0) as client:
            for lei in self._leis:
                self._throttle()
                url = f"{RECORDS_URL}/{lei}"
                response = client.get(url)
                response.raise_for_status()
                yield RawPayload(data=response.content, source_uri=url, fetched_at=utcnow())
            for name in self._legal_names:
                self._throttle()
                response = client.get(
                    RECORDS_URL,
                    params={"filter[entity.legalName]": name, "page[size]": 10},
                )
                response.raise_for_status()
                yield RawPayload(
                    data=response.content,
                    source_uri=str(response.request.url),
                    fetched_at=utcnow(),
                )

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        doc: dict[str, Any] = json.loads(payload.data)
        records = doc.get("data")
        if records is None:
            raise ValueError("not a GLEIF JSON:API document")
        if isinstance(records, dict):
            records = [records]
        provenance = Provenance(
            source_system=self.meta.source_id,
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.API,
            extractor_version=self.parser_version,
            payload_hash=payload_hash,
        )
        as_of: date = payload.fetched_at.date()
        facts: list[Fact] = []
        for record in records:
            attributes = record.get("attributes", {})
            lei = attributes.get("lei")
            if not lei:
                continue
            subject = lei_subject(lei)
            entity = attributes.get("entity", {})
            registration = attributes.get("registration", {})
            values: dict[str, object] = {
                "gleif:legalName": (entity.get("legalName") or {}).get("name"),
                "gleif:jurisdiction": entity.get("jurisdiction"),
                "gleif:status": entity.get("status"),
                "gleif:legalForm": (entity.get("legalForm") or {}).get("id"),
                "gleif:country": (entity.get("legalAddress") or {}).get("country"),
                "gleif:registrationStatus": registration.get("status"),
                "gleif:nextRenewalDate": registration.get("nextRenewalDate"),
            }
            for field, value in values.items():
                facts.append(
                    Fact(
                        subject=subject,
                        field=field,
                        value=None if value is None else str(value),
                        effective_from=as_of,
                        knowledge_from=payload.fetched_at,
                        provenance_id=provenance.id,
                    )
                )
        return ParsedBatch(provenance=(provenance,), facts=tuple(facts))


def relationship_field(relationship_type: str) -> str:
    return f"gleif:rr:{relationship_type}"


def relationship_status_field(relationship_type: str) -> str:
    return f"gleif:rr:{relationship_type}:status"


def _text(node: Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _relationship_period(rel: Element) -> tuple[date | None, date | None]:
    """The ``RELATIONSHIP_PERIOD`` entry bounds the edge's existence; the
    ``ACCOUNTING_PERIOD`` entries describe fiscal-year-specific consolidation
    metadata, not relationship lifetime (RR-CDF 2.1) — only the former sets
    ``effective_from``/``effective_to``."""
    periods = rel.find("rr:RelationshipPeriods", RR_NS)
    if periods is None:
        return None, None
    for period in periods.findall("rr:RelationshipPeriod", RR_NS):
        if _text(period.find("rr:PeriodType", RR_NS)) != "RELATIONSHIP_PERIOD":
            continue
        start_raw = _text(period.find("rr:StartDate", RR_NS))
        end_raw = _text(period.find("rr:EndDate", RR_NS))
        start = date.fromisoformat(start_raw[:10]) if start_raw else None
        end = date.fromisoformat(end_raw[:10]) if end_raw else None
        return start, end
    return None, None


class UnsupportedRelationshipNodeError(Exception):
    """An RR-CDF node id type other than LEI was observed — a schema
    change from the file verified 2026-07-27, needing explicit handling
    rather than a guess."""


class GleifRelationshipAdapter(SourceAdapter):
    """GLEIF Level 2 Relationship Record bulk file — parent/subsidiary and
    fund entity graph (§9.5). Feeds ``treble.core.entity_graph``."""

    meta = SourceMeta(
        source_id="gleif-rr",
        # the concatenated relationship file is republished daily.
        expected_cadence_days=1.0,
        description=(
            "GLEIF Level 2 Relationship Record (RR-CDF) bulk concatenated file "
            "— entity consolidation, branch, sub-fund and feeder relationships"
        ),
        licence="CC0 — GLEIF data is fully open",
        redistribution_restricted=False,
        rate_limit_per_second=1.0,
    )
    parser_version = "1"

    def fetch(self) -> Iterator[RawPayload]:
        with httpx.Client(timeout=180.0) as client:
            self._throttle()
            meta_response = client.get(CONCATENATED_META_URL)
            meta_response.raise_for_status()
            publishes = meta_response.json().get("data") or []
            if not publishes:
                raise ValueError("GLEIF concatenated-files/rr metadata returned no publishes")
            latest = max(publishes, key=lambda p: p["content_date"])

            self._throttle()
            url = CONCATENATED_DOWNLOAD_URL.format(file_id=latest["id"])
            response = client.get(url)
            response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                xml_names = [name for name in archive.namelist() if name.endswith(".xml")]
                if len(xml_names) != 1:
                    raise ValueError(
                        f"expected exactly one XML member in the RR zip, found {xml_names}"
                    )
                data = archive.read(xml_names[0])
            yield RawPayload(data=data, source_uri=url, fetched_at=utcnow())

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        root = safe_fromstring(payload.data)
        records_node = root.find("rr:RelationshipRecords", RR_NS)
        if records_node is None:
            raise ValueError("not an RR-CDF document: no RelationshipRecords element")

        provenance = Provenance(
            source_system=self.meta.source_id,
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.BULK_FILE,
            extractor_version=self.parser_version,
            payload_hash=payload_hash,
        )
        facts: list[Fact] = []
        malformed_periods = 0
        for record in records_node.findall("rr:RelationshipRecord", RR_NS):
            rel = record.find("rr:Relationship", RR_NS)
            if rel is None:
                continue
            start = rel.find("rr:StartNode", RR_NS)
            end = rel.find("rr:EndNode", RR_NS)
            start_id_type = _text(start.find("rr:NodeIDType", RR_NS)) if start is not None else ""
            end_id_type = _text(end.find("rr:NodeIDType", RR_NS)) if end is not None else ""
            if start_id_type != "LEI" or end_id_type != "LEI":
                raise UnsupportedRelationshipNodeError(
                    f"RR node id type {start_id_type!r}/{end_id_type!r} is not LEI"
                )
            start_lei = _text(start.find("rr:NodeID", RR_NS)) if start is not None else ""
            end_lei = _text(end.find("rr:NodeID", RR_NS)) if end is not None else ""
            relationship_type = _text(rel.find("rr:RelationshipType", RR_NS))
            status_raw = _text(rel.find("rr:RelationshipStatus", RR_NS))
            if not (start_lei and end_lei and relationship_type):
                continue

            period_start, period_end = _relationship_period(rel)
            # An end with no start: GLEIF records a RelationshipPeriod
            # whose EndDate is filled and StartDate is not, for a
            # relationship that has already lapsed. Falling back to the
            # fetch date there asserts the relationship *began today* and
            # ended in the past, which `Fact` rejects outright -- and
            # rightly, since it is not a period. The narrowest statement
            # consistent with what GLEIF actually says is a single day at
            # the known end.
            #
            # Found by running this adapter against the live concatenated
            # file (663,410 records) rather than the fixtures, which carry
            # no such record.
            effective_from = period_start or period_end or payload.fetched_at.date()
            if period_end is not None and period_end < effective_from:
                # A filer has reported a relationship that ended before it
                # began. That is not a period, and `Fact` refuses it -- so
                # the record is skipped and counted rather than repaired by
                # swapping the dates, which would invent a lifetime GLEIF
                # never asserted.
                #
                # Found by running against the live concatenated file
                # (663,410 records); the fixtures carry no such record, so
                # every test passed while a full ingest could not complete.
                malformed_periods += 1
                continue
            subject = lei_subject(start_lei)

            # Per-relationship values are bound as default arguments, not
            # captured: a late-binding closure would attribute every edge to
            # the *last* relationship's entity and period if these were ever
            # called after the loop.
            def emit(
                field: str,
                value: str | None,
                *,
                subject: TUID = subject,
                effective_from: date = effective_from,
                period_end: date | None = period_end,
            ) -> None:
                facts.append(
                    Fact(
                        subject=subject,
                        field=field,
                        value=value,
                        effective_from=effective_from,
                        effective_to=period_end,
                        knowledge_from=payload.fetched_at,
                        provenance_id=provenance.id,
                    )
                )

            emit(relationship_field(relationship_type), end_lei.upper())
            emit(relationship_status_field(relationship_type), status_raw or None)
        return ParsedBatch(provenance=(provenance,), facts=tuple(facts))
