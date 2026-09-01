"""OpenFIGI mapping adapter (spec §9.3; CLAUDE.md §6).

FIGI is the primary instrument key: free, openly redistributable, never
reused. Mapping results are cached permanently — a FIGI, once assigned,
never changes, so the local cache never needs invalidation.

The stored payload is a request+response **envelope**: OpenFIGI responses
are positionally aligned with the submitted jobs, so a response alone is
not interpretable. Storing both keeps ``parse`` a pure function of the
payload (I5) and replay byte-identical.

Rate limits: 25 mapping requests/minute unauthenticated, 250/minute with a
free API key; up to 100 jobs per request either way. Batch aggressively.
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

MAPPING_URL = "https://api.openfigi.com/v3/mapping"
_JOBS_PER_REQUEST = 100

# Response keys we persist, under as-published names (no invented mnemonics).
_RESULT_FIELDS = (
    "figi",
    "compositeFIGI",
    "shareClassFIGI",
    "name",
    "ticker",
    "exchCode",
    "securityType",
    "securityType2",
    "marketSector",
)


def figi_subject(figi: str) -> TUID:
    return TUID(f"figi:{figi.upper()}")


#: The effective period every FIGI mapping is filed under.
#:
#: `date.min`, and open-ended: **a FIGI never changes** (CLAUDE.md §9.3,
#: which is also why the mapping cache never needs invalidation). The
#: mapping is not a fact about a day, so it must not be filed under one.
#:
#: It was, and the cost was measured on the live store. `effective_from`
#: was `payload.fetched_at.date()`, so each fetch of the *same*
#: content-addressed payload created a new effective period — a different
#: partition, which write-path coalescing cannot collapse because a
#: different `effective_from` is a different assertion. 5,877 mappings were
#: stored 17,631 times across two fetch dates, and `subject_facts` on a
#: FIGI subject returned every field twice.
#:
#: A sentinel rather than a plausible-looking epoch: 1970 could be mistaken
#: for a claim about when the instrument existed, and nothing here knows
#: that. `date.min` reads as "for all the time this store can speak about",
#: which is exactly the claim being made.
#:
#: **Errors keep the fetch date**, deliberately. An identifier that failed
#: to map today may map next month, so *that* is a fact about a day.
MAPPING_PERIOD_START = date.min


class OpenFigiAdapter(SourceAdapter):
    meta = SourceMeta(
        source_id="openfigi",
        # an identifier lookup, not a feed.
        expected_cadence_days=None,
        description="OpenFIGI v3 mapping API (ANSI X9.145)",
        licence="Open Symbology terms; FIGIs are openly redistributable",
        redistribution_restricted=False,
        rate_limit_per_second=25.0 / 60.0,  # keyless tier; key raises to 250/min
    )
    #: 2 — mappings filed under a stable effective period rather than the
    #: fetch date. See `MAPPING_PERIOD_START`: version 1 stored 5,877
    #: mappings 17,631 times because each fetch minted a new partition.
    parser_version = "2"

    def __init__(
        self,
        payloads: PayloadStore,
        log: IngestLog,
        *,
        jobs: tuple[dict[str, str], ...],
        api_key: str | None = None,
    ) -> None:
        super().__init__(payloads, log)
        self._jobs = jobs
        self._api_key = api_key
        if api_key:
            # Authenticated tier: 250/min.
            self._bucket_rate = 250.0 / 60.0

    def fetch(self) -> Iterator[RawPayload]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["X-OPENFIGI-APIKEY"] = self._api_key
        with httpx.Client(timeout=60.0, headers=headers) as client:
            for start in range(0, len(self._jobs), _JOBS_PER_REQUEST):
                batch = list(self._jobs[start : start + _JOBS_PER_REQUEST])
                self._throttle()
                response = client.post(MAPPING_URL, json=batch)
                response.raise_for_status()
                envelope = json.dumps(
                    {"jobs": batch, "results": response.json()}, sort_keys=True
                ).encode()
                yield RawPayload(data=envelope, source_uri=MAPPING_URL, fetched_at=utcnow())

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        envelope: Any = json.loads(payload.data)
        if not isinstance(envelope, dict):
            raise ValueError("not an OpenFIGI request/response envelope")
        jobs = envelope.get("jobs")
        results = envelope.get("results")
        if jobs is None or results is None or len(jobs) != len(results):
            raise ValueError("not an OpenFIGI request/response envelope")
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
        for job, result in zip(jobs, results, strict=True):
            if "error" in result:
                # Unmapped identifiers are surfaced, never dropped silently:
                # a null fact records that the mapping was attempted and failed.
                facts.append(
                    Fact(
                        subject=TUID(f"unmapped:{job.get('idType')}:{job.get('idValue')}"),
                        field="openfigi:error",
                        value=str(result["error"]),
                        effective_from=as_of,
                        knowledge_from=payload.fetched_at,
                        provenance_id=provenance.id,
                    )
                )
                continue
            for row in result.get("data", []):
                figi = row.get("figi")
                if not figi:
                    continue
                subject = figi_subject(figi)
                for key in _RESULT_FIELDS:
                    if key == "figi":
                        continue
                    value = row.get(key)
                    facts.append(
                        Fact(
                            subject=subject,
                            field=f"openfigi:{key}",
                            value=None if value is None else str(value),
                            effective_from=MAPPING_PERIOD_START,
                            knowledge_from=payload.fetched_at,
                            provenance_id=provenance.id,
                        )
                    )
                # The mapping edge itself: which external id resolved here.
                facts.append(
                    Fact(
                        subject=subject,
                        field=f"openfigi:mapped:{job.get('idType', 'UNKNOWN')}",
                        value=str(job.get("idValue", "")),
                        effective_from=MAPPING_PERIOD_START,
                        knowledge_from=payload.fetched_at,
                        provenance_id=provenance.id,
                    )
                )
        return ParsedBatch(provenance=(provenance,), facts=tuple(facts))
