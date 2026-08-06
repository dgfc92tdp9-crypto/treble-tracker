"""Kenneth R. French Data Library — factor and industry return panels.

    Copyright Eugene F. Fama and Kenneth R. French.

**Why this source exists in this repository.** `PORT`/`TFM3` needs a K x K
factor covariance estimated from a return panel (spec §16.3), and the store
had no return panel of any kind: `PX_LAST` covered 36 FRED index series, 3
FX pairs and 2 crypto pairs, none of which are equity factors, and N-PORT
implied marks gave three report dates — two return observations per name.
That was recorded as a hard block on `P2_3`.

It is a block on *per-name* history, and the covariance does not need per-name
history. Fama and French publish the factor returns themselves, daily since
1963-07-01, along with industry portfolios whose own return series make them
usable as test assets. That is the panel the estimator was missing.

**What is deliberately not claimed.** This does not restore per-name equity
prices. A portfolio of individual stocks still cannot be risk-decomposed here,
because estimating a stock's factor exposures needs that stock's return
history. What it supports is a portfolio expressed over the published
portfolios — which is a real risk model on real data, and is not the same
thing as covering the equity universe.

**Terms.** Checked 2026-08-04 rather than assumed, because two sources have
already been refused on this project for their access terms:

- `robots.txt` at mba.tuck.dartmouth.edu disallows `/mbapo/`, `/fao/`,
  `/campaign/`, several archived course paths, and two other faculty
  directories. `/pages/faculty/ken.french/` is **not** among them, so
  automated retrieval is permitted by the site's own robots policy.
- The data pages carry no terms-of-use or licence statement. The HTML
  comment reserving rights ("All images and code are property of Ken
  French") is on the site's markup and graphics, not the data files.
- Every file is stamped `Copyright <year> Eugene F. Fama and Kenneth R.
  French`, so the data is copyrighted and this adapter is marked
  `redistribution_restricted`. The bulk-export guard withholds it, exactly
  as it withholds DTCC — the point of that flag is that a source can be
  used for analysis without being re-published.

Rate limited to one request every two seconds: this is a university web
server hosting a public good, not an API with a published quota.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from datetime import date, datetime
from typing import Final

import httpx

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.ingest.base import ParsedBatch, RawPayload, SourceAdapter, SourceMeta, utcnow
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore

BASE_URL: Final = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp"

#: The files this adapter knows how to read, and the subject prefix each
#: writes under. Keyed by the archive name so the URL and the fixture name
#: are the same string in both directions.
DATASETS: Final[dict[str, str]] = {
    "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip": "factor",
    "F-F_Momentum_Factor_daily_CSV.zip": "factor",
    "49_Industry_Portfolios_daily_CSV.zip": "portfolio",
}

#: Ken French's own missing-data sentinels, quoted from the file preamble:
#: "Missing data are indicated by -99.99 or -999". They are *returns in
#: percent*, so -99.99 is a legal-looking figure — an industry that lost
#: 99.99% in a day is not distinguishable from missing data by magnitude, and
#: treating one as the other puts a catastrophic fake return into a covariance.
#: Compared before the percent conversion, on the scale the file states them.
MISSING: Final[frozenset[float]] = frozenset({-99.99, -999.0})

#: Returns arrive in percent. Stored as decimals, because every other rate in
#: this system is a decimal and a panel mixing the two would compound wrongly
#: without anything looking wrong.
PERCENT: Final = 100.0


def _clean(name: str) -> str:
    """A column header as a field-safe token: `Mkt-RF` -> `MKT_RF`."""
    return name.strip().upper().replace("-", "_").replace(" ", "_")


def parse_french_csv(text: str) -> tuple[list[str], list[tuple[date, list[float | None]]]]:
    """(column names, [(date, values)]) from one Ken French CSV.

    These files are not machine-first: a prose preamble of unpredictable
    length, then a header row, then daily rows, then a copyright line — and
    several files hold a *second* table (annual, or equal-weighted) after a
    blank line and a fresh preamble. Only the first table is read, and the
    parse stops at the first non-data row rather than skipping it, so a file
    that gains a second section cannot silently contribute its rows to the
    first one's series.
    """
    columns: list[str] = []
    rows: list[tuple[date, list[float | None]]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            # A blank line after data has started ends the first table.
            if rows:
                break
            continue
        head = stripped.split(",")[0].strip()
        if not columns:
            # The header is the first row whose leading cell is empty — the
            # date column has no name in these files.
            if stripped.startswith(","):
                columns = [_clean(c) for c in stripped.split(",")[1:] if c.strip()]
            continue
        if not (len(head) == 8 and head.isdigit()):
            # Prose, a copyright line, or an annual table's YYYY key. Data
            # has started, so this is the end of it.
            if rows:
                break
            continue
        cells = [c.strip() for c in stripped.split(",")[1:]]
        values: list[float | None] = []
        for cell in cells[: len(columns)]:
            try:
                number = float(cell)
            except ValueError:
                values.append(None)
                continue
            values.append(None if number in MISSING else number / PERCENT)
        rows.append((date(int(head[:4]), int(head[4:6]), int(head[6:])), values))
    if not columns:
        raise ValueError("no header row found; the file layout has changed")
    if not rows:
        raise ValueError("no daily rows found; the file layout has changed")
    return columns, rows


class FrenchDataAdapter(SourceAdapter):
    """Daily factor and industry-portfolio returns.

    One `TOT_RETURN` fact per (series, day). The series is the subject and
    the day is both `effective_from` and `effective_to`, because a daily
    return is a measurement of one day and carries no validity beyond it —
    the same shape N-PORT marks use.
    """

    meta = SourceMeta(
        source_id="frenchdata",
        description="Kenneth R. French Data Library — Fama/French factors and industry portfolios",
        licence=(
            "No terms-of-use statement is published on the data pages. Files are stamped "
            "'Copyright Eugene F. Fama and Kenneth R. French'. robots.txt permits automated "
            "retrieval of /pages/faculty/ken.french/ (checked 2026-08-04). Marked "
            "redistribution-restricted on the copyright notice: usable for analysis here, "
            "withheld by the bulk-export guard."
        ),
        redistribution_restricted=True,
        rate_limit_per_second=0.5,
    )
    parser_version = "1.0"

    def __init__(
        self,
        payloads: PayloadStore,
        log: IngestLog,
        *,
        datasets: tuple[str, ...] = tuple(DATASETS),
        timeout: float = 120.0,
    ) -> None:
        super().__init__(payloads, log)
        unknown = sorted(set(datasets) - set(DATASETS))
        if unknown:
            raise ValueError(
                f"unknown Ken French dataset(s): {', '.join(unknown)}. "
                f"Known: {', '.join(sorted(DATASETS))}"
            )
        self._datasets = datasets
        self._timeout = timeout

    def fetch(self) -> Iterator[RawPayload]:
        headers = {"User-Agent": "TrebleTracker/0.1 (jack_treble@icloud.com)"}
        with httpx.Client(timeout=self._timeout, headers=headers) as client:
            for name in self._datasets:
                self._throttle()
                url = f"{BASE_URL}/{name}"
                response = client.get(url)
                response.raise_for_status()
                yield RawPayload(data=response.content, source_uri=url, fetched_at=utcnow())

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        name = payload.source_uri.rsplit("/", 1)[-1]
        kind = DATASETS.get(name)
        if kind is None:
            raise ValueError(f"no parser for {name!r}; it is not a known Ken French dataset")

        with zipfile.ZipFile(io.BytesIO(payload.data)) as archive:
            members = archive.namelist()
            if len(members) != 1:
                raise ValueError(f"{name}: expected one CSV in the archive, found {members}")
            text = archive.read(members[0]).decode("latin-1")

        columns, rows = parse_french_csv(text)
        record = Provenance(
            source_system=self.meta.source_id,
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.BULK_FILE,
            extractor_version=self.parser_version,
            payload_hash=payload_hash,
        )
        # `knowledge_from` is the retrieval time, not the observation day.
        # These series are *revised*: a CRSP refresh restates history, and
        # dating knowledge by the observation day would make a restatement
        # indistinguishable from the original (I2).
        knowledge_from: datetime = payload.fetched_at

        facts: list[Fact] = []
        for day, values in rows:
            for column, value in zip(columns, values, strict=False):
                if value is None:
                    continue
                facts.append(
                    Fact(
                        subject=TUID(f"{kind}:{column}"),
                        field="TOT_RETURN",
                        value=value,
                        effective_from=day,
                        effective_to=day,
                        knowledge_from=knowledge_from,
                        provenance_id=record.id,
                    )
                )
        return ParsedBatch(provenance=(record,), facts=tuple(facts))


__all__ = [
    "BASE_URL",
    "DATASETS",
    "MISSING",
    "FrenchDataAdapter",
    "parse_french_csv",
]
