"""US Treasury Daily Par Yield Curve Rates (spec §11.1).

The CMT curve: the constant-maturity par yields Treasury publishes at the
close of every business day, and the most widely cited government curve in
existence. Fourteen tenors from one month to thirty years.

Chosen for durability rather than convenience, which is the whole point of
this adapter. It is a US federal government work — public domain under 17
USC 105, with no licence to be withdrawn and no free tier to be
discontinued. It is served from `home.treasury.gov` with no API key, so
there is no credential to rotate, expire, or leak. `robots.txt` there
disallows only Drupal admin paths, so automated retrieval is permitted
rather than merely unmentioned. And it has been published every business
day since 1990, which is a longer track record than most of the vendors
that would sell it back.

That combination — public domain, keyless, robots-clean, three decades of
continuity — is the standard the rest of the source list should be judged
against. Compare the FRED graph-CSV endpoint this partly displaces: same
data for the Treasury series, but reached through an undocumented URL that
exists to serve a chart download button on a web page.

**This is not the swap curve.** It is a government curve, and pricing a
swap off it would omit the swap spread entirely. `SWPM` discounts off the
DTCC-built OIS curves; this feeds the government-benchmark side —
`ICVS`, G-spread, and a par curve to measure asset swaps against.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Final

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

CSV_URL: Final = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all"
    "?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv"
)

#: The curve every tenor hangs off. One subject per tenor rather than one
#: per curve: a tenor is the thing that has a value on a date, and the
#: store's shape is (subject, field, value, date).
CURVE: Final = "UST-CMT"

#: Column header to tenor label. Treasury writes them for a human — "1 Mo",
#: "1.5 Month", "10 Yr" — and the mapping is explicit because a parser that
#: derived it would silently accept a new column it did not understand.
#: "1.5 Month" was added in 2024 and is exactly that case.
TENORS: Final[dict[str, str]] = {
    "1 Mo": "1M",
    "1.5 Month": "6W",
    "2 Mo": "2M",
    "3 Mo": "3M",
    "4 Mo": "4M",
    "6 Mo": "6M",
    "1 Yr": "1Y",
    "2 Yr": "2Y",
    "3 Yr": "3Y",
    "5 Yr": "5Y",
    "7 Yr": "7Y",
    "10 Yr": "10Y",
    "20 Yr": "20Y",
    "30 Yr": "30Y",
}

FIELD: Final = "PAR_YIELD"


class UnknownColumnError(ValueError):
    """Treasury published a column this parser does not map.

    Raised rather than skipped. A silently dropped column is how a curve
    loses a tenor without anyone noticing — the fit still succeeds, the
    screen still renders, and the shape is subtly wrong at the end nobody
    was looking at. Treasury added "1.5 Month" in 2024 with no
    announcement this repository would have seen, so this is a thing that
    demonstrably happens rather than a hypothetical.
    """


def _month_day_year(stamp: str) -> tuple[int, int, int]:
    """Split Treasury's ``MM/DD/YYYY`` into (month, day, year) integers.

    Hand-parsed rather than `strptime`, which builds a naive datetime the
    project's DTZ rules rightly forbid. The published value is a calendar
    date with no time of day, and giving it one would invent a fact.
    """
    month, day, year = stamp.strip().split("/")
    return int(month), int(day), int(year)


def tenor_subject(tenor: str) -> TUID:
    """Deterministic TUID for a curve point: replay-stable (I5)."""
    return TUID(f"govt:{CURVE}:{tenor}")


class TreasuryCurveAdapter(SourceAdapter):
    """Daily par yields for one or more calendar years."""

    meta = SourceMeta(
        source_id="treasury-curve",
        description="US Treasury Daily Par Yield Curve Rates (CMT), 14 tenors",
        licence="US federal government work; public domain under 17 USC 105",
        redistribution_restricted=False,
        rate_limit_per_second=2.0,
        # Published at the close of every business day since 1990.
        expected_cadence_days=1.0,
    )
    parser_version = "1"

    def __init__(
        self,
        payloads: PayloadStore,
        log: IngestLog,
        *,
        years: tuple[int, ...] | None = None,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(payloads, log)
        self._years = years or (datetime.now(UTC).year,)
        self._timeout = timeout

    def fetch(self) -> Iterator[RawPayload]:
        for year in self._years:
            url = CSV_URL.format(year=year)
            with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
                response = client.get(url)
                response.raise_for_status()
                yield RawPayload(data=response.content, source_uri=url, fetched_at=utcnow())

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        record = Provenance(
            source_system=self.meta.source_id,
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.BULK_FILE,
            extractor_version=self.parser_version,
            payload_hash=str(payload_hash),
        )
        rows = list(csv.DictReader(io.StringIO(payload.data.decode("utf-8-sig"))))
        if not rows:
            # An empty CSV is a 200 that says nothing, which is what a
            # changed URL or a blocked client looks like. Treated as a
            # parse failure so it cannot be logged as a successful fetch.
            raise ValueError(f"{payload.source_uri} returned no rows")

        unknown = sorted(set(rows[0]) - set(TENORS) - {"Date"})
        if unknown:
            raise UnknownColumnError(
                f"Treasury published column(s) {', '.join(unknown)} which this parser does "
                f"not map (version {self.parser_version}). Extend TENORS rather than "
                "ignoring them: a dropped column removes a point from the curve, and the "
                "fit succeeds regardless"
            )

        facts: list[Fact] = []
        for row in rows:
            # A calendar date, not an instant: the curve is published for
            # a trading day, and attaching a timezone would invent a time
            # of day Treasury never stated.
            month, day, year = _month_day_year(row["Date"])
            when = date(year, month, day)
            for column, tenor in TENORS.items():
                raw = (row.get(column) or "").strip()
                if not raw:
                    # Treasury leaves a cell blank when a tenor was not
                    # published that day — the 30Y was suspended entirely
                    # from 2002 to 2006. Absent is not zero.
                    continue
                facts.append(
                    Fact(
                        subject=str(tenor_subject(tenor)),
                        field=FIELD,
                        # Published in percent; stored as a decimal rate, so
                        # every rate in this system is the same unit.
                        value=float(raw) / 100.0,
                        effective_from=when,
                        effective_to=when,
                        knowledge_from=payload.fetched_at,
                        provenance_id=record.id,
                    )
                )
        return ParsedBatch(provenance=(record,), facts=tuple(facts))


def latest_curve(facts: list[Fact]) -> dict[str, float]:
    """The most recent complete observation, tenor to rate. For tests."""
    if not facts:
        return {}
    day: date = max(f.effective_from for f in facts)
    return {
        str(f.subject).rsplit(":", 1)[1]: float(f.value)
        for f in facts
        if f.effective_from == day and isinstance(f.value, float)
    }


__all__ = [
    "CSV_URL",
    "CURVE",
    "FIELD",
    "TENORS",
    "TreasuryCurveAdapter",
    "UnknownColumnError",
    "latest_curve",
    "tenor_subject",
]
