"""ECB Statistical Data Warehouse — official euro reference rates (spec §9.1).

The European Central Bank publishes its daily reference rates through an
SDMX API with no key and no rate limit, and every observation arrives with
its own provenance metadata: the compiling organisation, the publication
time (2.15 pm CET), and the exact series definition.

**Why this source rather than an FX aggregator.** A reference rate is not a
tradeable quote and does not pretend to be one — it is the ECB's own daily
fixing, used for accounting and settlement across the euro area. That makes
it a *primary* source in the sense this project cares about: there is no
vendor between the publisher and the fact, so `SPTR` traces a rate to the
institution that set it.

**What it is not.** Not a market price. The 2.15 pm fixing is one observation
per business day, so it must never be presented as a live rate or used to
mark a position intraday. The screens that consume it say so.
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
from treble.ingest.base import ParsedBatch, RawPayload, SourceAdapter, SourceMeta, utcnow
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore

#: SDMX data endpoint. The key selects frequency.currency.denominator.type.
SERIES_URL = "https://data-api.ecb.europa.eu/service/data/EXR/{key}"


def fx_subject(key: str) -> TUID:
    """`D.USD.EUR.SP00.A` -> `fx:USDEUR`.

    The pair, not the SDMX key: a subject should name the thing, and the
    frequency and series type belong to the observation rather than the
    instrument.
    """
    parts = key.split(".")
    return TUID(f"fx:{parts[1]}{parts[2]}") if len(parts) >= 3 else TUID(f"fx:{key}")


class EcbExchangeRatesAdapter(SourceAdapter):
    """Daily euro foreign exchange reference rates."""

    meta = SourceMeta(
        source_id="ecb-fx",
        description="ECB SDMX euro foreign exchange reference rates",
        licence="Free reuse with attribution to the ECB; no redistribution limit",
        redistribution_restricted=False,
        rate_limit_per_second=2.0,
    )
    parser_version = "1"

    def __init__(self, payloads: PayloadStore, log: IngestLog, *, series: tuple[str, ...]) -> None:
        super().__init__(payloads, log)
        self._series = series

    def fetch(self) -> Iterator[RawPayload]:
        for key in self._series:
            self._throttle()
            url = SERIES_URL.format(key=key)
            response = httpx.get(
                url, params={"format": "csvdata"}, timeout=120.0, follow_redirects=True
            )
            response.raise_for_status()
            yield RawPayload(data=response.content, source_uri=url, fetched_at=utcnow())

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        provenance = Provenance(
            source_system=self.meta.source_id,
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.API,
            extractor_version=self.parser_version,
            payload_hash=payload_hash,
        )
        facts: list[Fact] = []
        reader = csv.DictReader(io.StringIO(payload.data.decode("utf-8", errors="replace")))
        for row in reader:
            key, when, raw = row.get("KEY"), row.get("TIME_PERIOD"), row.get("OBS_VALUE")
            if not key or not when or not raw:
                continue
            try:
                value = float(raw)
                observed = date.fromisoformat(when)
            except ValueError:
                # A non-numeric observation is a gap in the ECB's own series
                # (a euro-area holiday), not a value to coerce to zero.
                continue
            facts.append(
                Fact(
                    subject=fx_subject(key.removeprefix("EXR.")),
                    field="PX_LAST",
                    value=value,
                    effective_from=observed,
                    effective_to=observed,
                    # The fixing is published the same day it describes; the
                    # payload carries no per-observation timestamp, so the
                    # retrieval time is the honest knowledge date.
                    knowledge_from=payload.fetched_at,
                    provenance_id=provenance.id,
                )
            )
        return ParsedBatch(provenance=(provenance,), facts=tuple(facts))


def observation_dates(payload: bytes) -> list[datetime]:  # pragma: no cover - helper
    """Parse helper kept for diagnostics; not used by the ingest path."""
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8", errors="replace")))
    return [datetime.fromisoformat(row["TIME_PERIOD"]) for row in reader if row.get("TIME_PERIOD")]
