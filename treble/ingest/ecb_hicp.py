"""ECB HICP — the euro area's published price index (spec §9.1, §12.1).

The inflation swap pricer shipped and had no index to price against; the
reachability sweep recorded it as data-blocked. This is the data. Same SDMX
API, same terms and same absence of a key as the FX adapter already uses —
`data-api.ecb.europa.eu`, free reuse with attribution.

**The index, not the annual rate.** ECB publishes both `INX` (the level) and
`ANR` (year-on-year change). A zero-coupon inflation swap pays
`I_T / I_0 - 1` off *levels*, so storing the rate would force every consumer
to reconstruct a level from a percentage and a base nobody recorded. Both
are ingested because a screen wants the rate and a swap wants the level, and
they are stored under different fields so nothing can read one as the other.

**A monthly observation is a month, not a day.** `TIME_PERIOD` arrives as
`2025-10`. It is stored spanning the whole month — effective from the first
to the last — because that is what the figure describes. Collapsing it to a
single day would make a point-in-time read on the 15th miss an observation
that was true of the 15th.

**Publication lags the month it measures, and that is the whole reason
inflation swaps carry an index lag.** October's HICP appears in November.
`knowledge_from` is the retrieval time, which is honest: the payload carries
no per-observation publication timestamp, and inventing one from a rule
about release calendars would be a guess wearing a fact's clothes. Which
month a contract references is
:attr:`~treble.analytics.derivatives.inflation.InflationSwapSpec.index_lag_months`,
and it is required there for exactly this reason.
"""

from __future__ import annotations

import calendar
import csv
import io
from collections.abc import Iterator
from datetime import date

import httpx

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.ingest.base import ParsedBatch, RawPayload, SourceAdapter, SourceMeta, utcnow
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore

SERIES_URL = "https://data-api.ecb.europa.eu/service/data/ICP/{key}"

#: Euro-area HICP, all items, monthly. The level and the year-on-year rate.
#: `U2` is the euro area, `000000` the all-items aggregate, `N` unadjusted —
#: the published headline, not a seasonally adjusted variant, because a swap
#: settles against what was published.
INDEX_KEY = "M.U2.N.000000.4.INX"
RATE_KEY = "M.U2.N.000000.4.ANR"

#: Stored under distinct fields so a level cannot be read as a rate. 129.7
#: and 2.1 are both plausible-looking numbers and mean entirely different
#: things.
INDEX_FIELD = "PX_LAST"
RATE_FIELD = "YOY_PCT"

SUBJECT = TUID("inflation:EUR:HICP")


class EcbHicpAdapter(SourceAdapter):
    """Euro-area HICP levels and year-on-year rates."""

    meta = SourceMeta(
        source_id="ecb-hicp",
        description="ECB SDMX euro-area HICP (all items, unadjusted)",
        licence="Free reuse with attribution to the ECB; no redistribution limit",
        redistribution_restricted=False,
        rate_limit_per_second=2.0,
    )
    parser_version = "1"

    def __init__(
        self,
        payloads: PayloadStore,
        log: IngestLog,
        *,
        keys: tuple[str, ...] = (INDEX_KEY, RATE_KEY),
        timeout: float = 60.0,
    ) -> None:
        super().__init__(payloads, log)
        self._keys = keys
        self._timeout = timeout

    def fetch(self) -> Iterator[RawPayload]:
        for key in self._keys:
            self._throttle()
            url = SERIES_URL.format(key=key)
            response = httpx.get(url, headers={"Accept": "text/csv"}, timeout=self._timeout)
            response.raise_for_status()
            yield RawPayload(data=response.content, source_uri=url, fetched_at=utcnow())

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        provenance = Provenance(
            source_system=self.meta.source_id,
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.API,
            extractor_version=self.parser_version,
            payload_hash=str(payload_hash),
        )
        facts: list[Fact] = []
        reader = csv.DictReader(io.StringIO(payload.data.decode("utf-8", errors="replace")))
        for row in reader:
            key, period, raw = row.get("KEY"), row.get("TIME_PERIOD"), row.get("OBS_VALUE")
            if not key or not period or not raw:
                continue
            span = _month_span(period)
            if span is None:
                continue
            try:
                value = float(raw)
            except ValueError:
                # A gap in the ECB's own series, not a value to coerce.
                continue
            first, last = span
            facts.append(
                Fact(
                    subject=SUBJECT,
                    field=RATE_FIELD if key.endswith("ANR") else INDEX_FIELD,
                    value=value,
                    # The whole month, because that is what the figure
                    # describes. A single day would make a point-in-time
                    # read mid-month miss an observation true of that day.
                    effective_from=first,
                    effective_to=last,
                    knowledge_from=payload.fetched_at,
                    provenance_id=provenance.id,
                )
            )
        if not facts:
            raise ValueError(
                f"{payload.source_uri}: parsed but produced no observations. An empty "
                "series and an unparsed one render the same and mean different things"
            )
        return ParsedBatch(provenance=(provenance,), facts=tuple(facts))


def _month_span(period: str) -> tuple[date, date] | None:
    """`2025-10` -> the first and last day of that month.

    Returns `None` rather than guessing on anything else. ECB serves other
    frequencies from the same dataflow, and silently reading a quarterly
    period as a month would file Q4 under October.
    """
    parts = period.split("-")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    year, month = int(parts[0]), int(parts[1])
    if not 1 <= month <= 12:
        return None
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


__all__ = [
    "INDEX_FIELD",
    "INDEX_KEY",
    "RATE_FIELD",
    "RATE_KEY",
    "SUBJECT",
    "EcbHicpAdapter",
]
