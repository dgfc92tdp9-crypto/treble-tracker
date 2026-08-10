"""FRED adapter (spec §8.1.1; CLAUDE.md §6).

Uses the keyless ``fredgraph.csv`` endpoint so local-only mode needs no API
key; the authenticated JSON API is a drop-in upgrade behind the same parse
contract.

I2 note: fredgraph payloads carry no publication timestamps, so
``knowledge_from`` is the fetch time — conservative (we provably knew the
value by then) and honest. ALFRED vintage dates are the Phase 2 upgrade for
true macro point-in-time history; recorded here rather than papered over.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import date, datetime

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

FRED_GRAPH_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def series_subject(series_id: str) -> TUID:
    """Deterministic TUID for a macro series: replay-stable (I5)."""
    return TUID(f"fred:{series_id.upper()}")


class FredAdapter(SourceAdapter):
    meta = SourceMeta(
        source_id="fred",
        # the configured set includes daily series such as DGS10.
        expected_cadence_days=1.0,
        description="Federal Reserve Economic Data, fredgraph CSV endpoint",
        licence="FRED terms of use; series carry their own source licences",
        redistribution_restricted=False,
        rate_limit_per_second=1.0,
    )
    parser_version = "1"

    def __init__(
        self,
        payloads: PayloadStore,
        log: IngestLog,
        *,
        series: tuple[str, ...],
        start: date,
        end: date,
    ) -> None:
        super().__init__(payloads, log)
        self._series = series
        self._start = start
        self._end = end

    def fetch(self) -> Iterator[RawPayload]:
        with httpx.Client(timeout=60.0) as client:
            for series_id in self._series:
                self._throttle()
                url = (
                    f"{FRED_GRAPH_URL}?id={series_id}"
                    f"&cosd={self._start.isoformat()}&coed={self._end.isoformat()}"
                )
                response = client.get(url)
                response.raise_for_status()
                yield RawPayload(data=response.content, source_uri=url, fetched_at=utcnow())

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        text = payload.data.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))
        header = next(reader)
        if len(header) != 2 or header[0].upper() not in ("DATE", "OBSERVATION_DATE"):
            raise ValueError(f"unrecognised fredgraph header: {header!r}")
        series_id = header[1].strip()
        provenance = Provenance(
            source_system=self.meta.source_id,
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.API,
            extractor_version=self.parser_version,
            payload_hash=payload_hash,
        )
        facts: list[Fact] = []
        subject = series_subject(series_id)
        for row in reader:
            if len(row) != 2:
                continue
            day = date.fromisoformat(row[0])
            raw_value = row[1].strip()
            # "." (classic) and "" (current) are FRED's missing-value markers:
            # store null with provenance, never invent a number.
            value: float | None = None if raw_value in (".", "") else float(raw_value)
            facts.append(
                Fact(
                    subject=subject,
                    field="PX_LAST",
                    value=value,
                    effective_from=day,
                    effective_to=day,
                    knowledge_from=payload.fetched_at,
                    provenance_id=provenance.id,
                )
            )
        return ParsedBatch(provenance=(provenance,), facts=tuple(facts))


def _require_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")
    return dt
