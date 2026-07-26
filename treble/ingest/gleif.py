"""GLEIF LEI adapter (spec §9.5; CLAUDE.md §6).

LEI is the primary *entity* key. This adapter handles record-level API
responses (JSON:API); the entity graph at all-filers scale uses GLEIF's
bulk concatenated files (including Level 2 relationship records) through
the same fact vocabulary — bulk iteration lands with the security-master
population work, per CLAUDE.md: take the bulk file, not the API, for the
graph.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
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

RECORDS_URL = "https://api.gleif.org/api/v1/lei-records"


def lei_subject(lei: str) -> TUID:
    return TUID(f"lei:{lei.upper()}")


class GleifAdapter(SourceAdapter):
    meta = SourceMeta(
        source_id="gleif",
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
            source_system="gleif",
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
