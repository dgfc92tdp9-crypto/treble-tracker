"""DTCC SDR public price dissemination — real swap rates (spec §11.1, §9.1).

    Swap rates from the DTCC SDR public feed. — spec §11.1

CFTC Part 43 requires swap data repositories to publish every reportable
swap transaction publicly and free of charge. DTCC Data Repository does so
through a daily cumulative file per asset class, and the interest-rate file
carries the fixed rate, both legs' conventions, the effective and expiration
dates, and the notional of roughly twenty thousand trades a day. That is
enough to build four curves out to thirty years — which is what `SWPM` needs
and what no other free source supplies: FRED discontinued its swap series in
2016, and the Treasury CMT curve is a government curve, not a swap curve.

    USD-SOFR-OIS     discounting
    EUR-ESTR-OIS     discounting
    EUR-EURIBOR-6M   forecasting, 6M index
    EUR-EURIBOR-3M   forecasting, 3M index

The euro pair is what makes `SWPM` work end to end. An overnight index
compounds daily, so an OIS curve discounts but cannot project a discrete
index — the pricer refuses it as a forecast curve. EURIBOR is a term index
and can. ESTR discounting against EURIBOR-6M forecasting is a genuine
multi-curve environment, and the basis between them is real: about 25bp at
ten years, narrowing to 12bp at thirty.

**The index tenor is not in the payload's index name.** `EUR-EURIBOR` is the
underlier for both 3M and 6M swaps, so nothing in `UPI Underlier Name`
separates them; only the floating leg's reset frequency does. Merging them
would blend two curves that a tenor basis genuinely separates by about 11bp,
and the merged curve would look entirely ordinary.

**These are transacted rates, not quotes, and the distinction is not
cosmetic.** A dealer quote is a price someone will trade at now. A print is
a price two people traded at, some of it off-market: on a single day the
10-year prints ranged from -0.50% to +0.70% around a 4.31% median, because
swaps trade at arbitrary fixed rates with a compensating upfront. So this
takes the **median** of a tenor's prints, never the mean, and publishes the
interquartile dispersion beside it so a reader can see how firm the point
is. A tenor with fewer than :data:`MIN_TRADES_PER_TENOR` prints is omitted
rather than published — one trade is not a curve point.

**What is filtered out, and why each would corrupt the curve silently:**

- *Lifecycle events.* Only new trades (`NEWT`/`TRAD`) are prints. Amendments,
  novations and terminations reference an existing trade and would count it
  twice. Prints later flagged as errors (`EROR`) are dropped by identifier.
- *Forward-starting trades.* A swap effective in six months has a forward
  par rate, not a spot one. Roughly half the file starts on IMM dates, so
  including them would drag the whole curve.
- *Non-standard conventions.* The fixed leg is annual ACT/360 for SOFR OIS.
  The same file carries 30/360 and ACT/365F trades whose rates are the same
  economics expressed in different units — blending them shifts the curve by
  the ratio between conventions (365/360 is 1.4%, about 6bp on a 4% rate).
  That is precisely the defect ADR-0006 found in the curve bootstrap.
- *A different index.* `USD-SOFR CME Term` and `USD-SOFR ICE Swap Rate` are
  not the SOFR OIS index. They are separate curves, not extra observations.

**Terms of use — read this before extending the adapter.** DTCC's own terms
live at `https://www.dtcc.com/legal.php`, which is served behind Cloudflare
bot protection and returns HTTP 403 to any non-browser client. Those terms
therefore could **not** be verified before this adapter was written, and
DTCC separately sells *OTC Direct Connect*, a paid systematic-delivery
product for this same dashboard data. Jack was shown both facts on
2026-08-01 and instructed the adapter be built anyway; that is a recorded
decision, not an oversight, and it is why this source is marked
``redistribution_restricted`` and throttled to one request every five
seconds. If the terms are later read and prohibit automated access, this
module is the thing to delete.
"""

from __future__ import annotations

import csv
import io
import re
import statistics
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date

import httpx
from pydantic import BaseModel, ConfigDict

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.ingest.base import ParsedBatch, RawPayload, SourceAdapter, SourceMeta, utcnow
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore

#: The dashboard's own file-list endpoint. `IR` is the interest-rate asset
#: class; the response names an S3 path per daily file. Used for discovery
#: only — population fetches :data:`FILE_URL` directly, so a step's URI is
#: predictable from its date and resumability does not depend on a listing.
LIST_URL = "https://pddata.dtcc.com/ppd/api/cumulative/{jurisdiction}/{asset_class}"

#: The daily cumulative file itself, on DTCC's public S3 bucket.
FILE_URL = "https://kgc0418-tdw-data-0.s3.amazonaws.com/cftc/eod/CFTC_CUMULATIVE_RATES_{stamp}.zip"


def file_url(report_date: date) -> str:
    """The cumulative interest-rate file for one trading day."""
    return FILE_URL.format(stamp=report_date.strftime("%Y_%m_%d"))


#: `CFTC_CUMULATIVE_RATES_2026_07_31.zip` -> 2026-07-31. The report date is
#: recoverable from the URI alone, which is what keeps `parse` a pure
#: function of the payload and its source (I5) — the CSV rows carry
#: execution timestamps but nothing that names the file's own trading day.
_FILENAME_DATE = re.compile(r"_(\d{4})_(\d{2})_(\d{2})\.zip", re.IGNORECASE)

#: A tenor needs this many prints before it is published. Three is not a
#: deep sample; it is the point below which a median stops being one.
MIN_TRADES_PER_TENOR = 3

#: How far a trade's effective date may sit from its execution date and
#: still count as spot-starting. Two business days plus a weekend.
SPOT_WINDOW_DAYS = 5

#: How close a trade's length must be to a whole number of years, in years.
#: Standard tenors only: a 7-year-3-month swap is a real trade and a
#: meaningless curve node.
TENOR_TOLERANCE_YEARS = 0.03

_DAYS_PER_YEAR = 365.25


class DtccParseError(ValueError):
    """The payload is not a readable cumulative file."""


#: ISO 20022 frequency periods, in months. `EXPI` (at expiry) and `ADHO`
#: (ad hoc) have no regular length and are deliberately absent — a trade
#: quoting one has no standard schedule and is not a curve observation.
_MONTHS_PER_PERIOD: dict[str, int] = {"MNTH": 1, "YEAR": 12}


def frequency_months(period: str | None, multiplier: str | None) -> int | None:
    """A payment or reset frequency in months, or None if irregular.

    Normalised because the file spells one frequency several ways: an
    annual leg appears as `YEAR`x1 and as `MNTH`x12, and both are annual.
    Comparing the raw pair would silently drop 11% of the ESTR prints and
    5% of the SOFR prints for no economic reason — an exclusion nobody
    would ever see, because the curve it left behind still looks complete.
    """
    months = _MONTHS_PER_PERIOD.get((period or "").strip().upper())
    if months is None:
        return None
    try:
        return months * int(multiplier or "")
    except ValueError:
        return None


@dataclass(frozen=True)
class CurveConvention:
    """One curve's definition in terms of the file's own columns.

    Explicit rather than inferred. The alternative — take whatever the
    majority of rows say — silently redefines the curve on any day the mix
    of trades shifts.
    """

    curve: str
    currency: str
    #: `UPI Underlier Name` values that are this index. Anything else is a
    #: different curve, not another observation of this one.
    underliers: frozenset[str]
    #: ISO 20022 day count code on the fixed leg. A004 is ACT/360, A001 is
    #: 30/360 — the EUR fixed-leg convention.
    fixed_day_count: str
    #: Fixed payments per year expressed in months. 12 is annual.
    fixed_frequency_months: int
    #: The floating leg's reset in months — 6 for 6M EURIBOR. `None` for an
    #: overnight index, which compounds daily and has no discrete reset; the
    #: underlier name already pins those, and filtering on a reset the index
    #: does not have would exclude everything.
    float_reset_months: int | None = None
    float_day_count: str | None = None
    #: The index tenor this curve forecasts, or None for an overnight
    #: discounting curve. This is what makes a curve usable as a *forecast*
    #: curve downstream: `SWPM` refuses an overnight index as one, because
    #: projecting a daily-compounded rate on a discrete schedule prices a
    #: different instrument.
    index_tenor: str | None = None


USD_SOFR_OIS = CurveConvention(
    curve="USD-SOFR-OIS",
    currency="USD",
    underliers=frozenset({"USD-SOFR-COMPOUND", "USD-SOFR-OIS Compound"}),
    fixed_day_count="A004",
    fixed_frequency_months=12,
)

#: The euro discounting curve. Both spellings of the underlier appear in the
#: same file on the same day and are the same index; treating them as two
#: curves would halve the prints behind every node.
EUR_ESTR_OIS = CurveConvention(
    curve="EUR-ESTR-OIS",
    currency="EUR",
    underliers=frozenset({"EUR-EuroSTR-COMPOUND", "EUR-EuroSTR-OIS Compound"}),
    fixed_day_count="A004",
    fixed_frequency_months=12,
)

#: The euro *forecast* curves. Standard EUR convention: annual 30/360 fixed
#: against ACT/360 floating. The index tenor is not in `UPI Underlier Name`
#: — `EUR-EURIBOR` alone says nothing about 3M or 6M — so it comes from the
#: floating leg's reset frequency. Two curves connected by a tenor basis,
#: which is exactly the structure spec §11.1 describes.
EUR_EURIBOR_6M = CurveConvention(
    curve="EUR-EURIBOR-6M",
    currency="EUR",
    underliers=frozenset({"EUR-EURIBOR", "EUR-EURIBOR-Reuters"}),
    fixed_day_count="A001",
    fixed_frequency_months=12,
    float_reset_months=6,
    float_day_count="A004",
    index_tenor="6M",
)

EUR_EURIBOR_3M = CurveConvention(
    curve="EUR-EURIBOR-3M",
    currency="EUR",
    underliers=frozenset({"EUR-EURIBOR", "EUR-EURIBOR-Reuters"}),
    fixed_day_count="A001",
    fixed_frequency_months=12,
    float_reset_months=3,
    float_day_count="A004",
    index_tenor="3M",
)

CONVENTIONS: tuple[CurveConvention, ...] = (
    USD_SOFR_OIS,
    EUR_ESTR_OIS,
    EUR_EURIBOR_6M,
    EUR_EURIBOR_3M,
)


class TenorObservation(BaseModel):
    """One curve point, with enough beside it to judge the point.

    `trades` and `dispersion_bp` are published as facts rather than kept as
    diagnostics: a 30-year node from four prints spanning 15bp and a 10-year
    node from 400 prints spanning 2bp are both "the median", and a screen
    that showed only the rate would present them as equally solid.
    """

    model_config = ConfigDict(frozen=True)

    curve: str
    tenor: str
    years: int
    rate: float
    trades: int
    #: Interquartile range in basis points — the spread of the prints this
    #: median came from, not a bid-offer.
    dispersion_bp: float
    #: Prints whose notional was capped under Part 43's block rules. Their
    #: *rate* is unaffected, which is why they are counted rather than
    #: dropped; a curve built only from small trades would be its own bias.
    capped_trades: int


def curve_subject(curve: str, tenor: str) -> TUID:
    """`USD-SOFR-OIS`, `10Y` -> `swap:USD-SOFR-OIS:10Y`."""
    return TUID(f"swap:{curve}:{tenor}")


def report_date_from_uri(uri: str) -> date:
    """The trading day a cumulative file describes."""
    match = _FILENAME_DATE.search(uri)
    if match is None:
        raise DtccParseError(
            f"no report date in {uri!r}: the file name is the only place the trading day "
            "appears, so without it every fact would be dated by when it was fetched"
        )
    year, month, day = (int(part) for part in match.groups())
    return date(year, month, day)


def _rows_from_zip(data: bytes) -> list[dict[str, str]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise DtccParseError("payload is not a zip archive") from exc
    names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
    if len(names) != 1:
        raise DtccParseError(f"expected exactly one CSV in the archive, found {names}")
    text = archive.read(names[0]).decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def _as_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _live_prints(rows: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    """New trades, with anything later flagged as an error removed.

    Errors are excluded by identifier rather than ignored: an `EROR` row
    says a previously disseminated print did not happen, and leaving it in
    the sample means pricing off a trade the reporting party has withdrawn.
    """
    withdrawn = {
        row.get("Original Dissemination Identifier", "")
        for row in rows
        if row.get("Action type") == "EROR"
    }
    withdrawn.discard("")
    return [
        row
        for row in rows
        if row.get("Action type") == "NEWT"
        and row.get("Event type") == "TRAD"
        and row.get("Dissemination Identifier", "") not in withdrawn
    ]


def par_rates(
    rows: Sequence[dict[str, str]], convention: CurveConvention, report_date: date
) -> tuple[TenorObservation, ...]:
    """Median transacted par rate per standard tenor.

    A pure function of the parsed rows: same file, same curve, every time
    (I5). Every exclusion it applies is named in the module docstring.
    """
    by_tenor: dict[int, list[tuple[float, bool]]] = {}
    for row in _live_prints(rows):
        if row.get("Notional currency-Leg 1") != convention.currency:
            continue
        if (row.get("UPI Underlier Name") or "") not in convention.underliers:
            continue
        if row.get("Fixed rate day count convention-leg 1") != convention.fixed_day_count:
            continue
        if (
            frequency_months(
                row.get("Fixed rate payment frequency period-Leg 1"),
                row.get("Fixed rate payment frequency period multiplier-Leg 1"),
            )
            != convention.fixed_frequency_months
        ):
            continue
        # The floating leg identifies the *index tenor* on a forecast curve.
        # `EUR-EURIBOR` names no tenor, so a 3M and a 6M swap are
        # indistinguishable by underlier — and blending them merges two
        # curves that a tenor basis separates by about 11bp.
        if convention.float_reset_months is not None:
            if (
                frequency_months(
                    row.get("Floating rate reset frequency period-leg 2"),
                    row.get("Floating rate reset frequency period multiplier-leg 2"),
                )
                != convention.float_reset_months
            ):
                continue
            if row.get("Floating rate day count convention-leg 2") != convention.float_day_count:
                continue

        executed = _as_date(row.get("Execution Timestamp"))
        effective = _as_date(row.get("Effective Date"))
        expiration = _as_date(row.get("Expiration Date"))
        if executed is None or effective is None or expiration is None:
            continue
        if executed != report_date:
            # A print carried in this file but executed on another day
            # belongs to that day's curve.
            continue
        if abs((effective - executed).days) > SPOT_WINDOW_DAYS:
            continue  # forward-starting: a forward rate, not a spot par rate

        years = (expiration - effective).days / _DAYS_PER_YEAR
        whole = round(years)
        if whole < 1 or abs(years - whole) > TENOR_TOLERANCE_YEARS:
            continue

        try:
            rate = float(row["Fixed rate-Leg 1"])
        except (KeyError, ValueError):
            continue
        capped = (row.get("Block trade election indicator") or "").upper() == "TRUE" or (
            row.get("Large notional off-facility swap election indicator") or ""
        ).upper() == "TRUE"
        by_tenor.setdefault(whole, []).append((rate, capped))

    observations: list[TenorObservation] = []
    for years_, samples in sorted(by_tenor.items()):
        if len(samples) < MIN_TRADES_PER_TENOR:
            # Omitted, not interpolated. A curve that is short is honest;
            # a curve with an invented node is not.
            continue
        rates = sorted(rate for rate, _ in samples)
        quartiles = statistics.quantiles(rates, n=4) if len(rates) >= 4 else [rates[0], rates[-1]]
        observations.append(
            TenorObservation(
                curve=convention.curve,
                tenor=f"{years_}Y",
                years=years_,
                rate=statistics.median(rates),
                trades=len(rates),
                dispersion_bp=abs(quartiles[-1] - quartiles[0]) * 1e4,
                capped_trades=sum(1 for _, capped in samples if capped),
            )
        )
    return tuple(observations)


class DtccSdrRatesAdapter(SourceAdapter):
    """Daily CFTC interest-rate cumulative files, reduced to curve points."""

    meta = SourceMeta(
        source_id="dtcc-sdr",
        description="DTCC Data Repository CFTC public price dissemination — interest rates",
        licence=(
            "CFTC Part 43 mandates free public dissemination. DTCC's own terms at "
            "dtcc.com/legal.php are served behind bot protection and could not be read; "
            "automated access is therefore UNVERIFIED, and DTCC sells a paid systematic-"
            "access product (OTC Direct Connect) for this same data. Built on Jack's "
            "explicit instruction of 2026-08-01 with those facts stated. Treated as "
            "redistribution-restricted."
        ),
        redistribution_restricted=True,
        # One request per five seconds. Slow on purpose: the daily files are
        # a handful of megabytes and there is no reason to be in a hurry
        # against a source whose terms are unread.
        rate_limit_per_second=0.2,
    )
    parser_version = "1"

    def __init__(
        self, payloads: PayloadStore, log: IngestLog, *, report_dates: tuple[date, ...]
    ) -> None:
        super().__init__(payloads, log)
        self._report_dates = report_dates

    def fetch(self) -> Iterator[RawPayload]:
        for report_date in self._report_dates:
            self._throttle()
            url = file_url(report_date)
            response = httpx.get(url, timeout=300.0, follow_redirects=True)
            response.raise_for_status()
            yield RawPayload(data=response.content, source_uri=url, fetched_at=utcnow())

    @staticmethod
    def trading_days(
        *, limit: int = 30, jurisdiction: str = "CFTC", asset_class: str = "IR"
    ) -> tuple[date, ...]:
        """Report dates with a real day's trading behind them, newest first.

        Discovery, not ingestion: it names *which* days exist so a universe
        can be planned, and the population path then fetches each day by its
        own predictable URL.

        Weekends and holidays appear in the listing with a handful of rows —
        late reports and lifecycle events on trades executed earlier. They
        are excluded here rather than downloaded and found empty, because a
        day with eleven rows yields no tenor that clears
        :data:`MIN_TRADES_PER_TENOR` and its absence from the curve would
        look like a gap rather than a holiday.
        """
        response = httpx.get(
            LIST_URL.format(jurisdiction=jurisdiction, asset_class=asset_class),
            timeout=120.0,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        days: list[date] = []
        for entry in response.json():
            name = str(entry.get("fileName") or "")
            if int(entry.get("rowCount") or 0) <= 1000:
                continue
            try:
                days.append(report_date_from_uri(name))
            except DtccParseError:  # a malformed file name is skipped, not fatal
                continue
        return tuple(days[:limit])

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        report_date = report_date_from_uri(payload.source_uri)
        rows = _rows_from_zip(payload.data)
        provenance = Provenance(
            source_system=self.meta.source_id,
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.API,
            extractor_version=self.parser_version,
            payload_hash=payload_hash,
        )
        facts: list[Fact] = []
        for convention in CONVENTIONS:
            for observation in par_rates(rows, convention, report_date):
                subject = curve_subject(observation.curve, observation.tenor)
                for field, value in (
                    ("PAR_RATE", observation.rate),
                    ("TRADE_COUNT", float(observation.trades)),
                    ("RATE_DISPERSION_BP", observation.dispersion_bp),
                    ("CAPPED_TRADE_COUNT", float(observation.capped_trades)),
                ):
                    facts.append(
                        Fact(
                            subject=subject,
                            field=field,
                            value=value,
                            effective_from=report_date,
                            effective_to=report_date,
                            # The file is published after the close of the
                            # day it describes, so the fetch time is the
                            # earliest moment this could have been known.
                            knowledge_from=payload.fetched_at,
                            provenance_id=provenance.id,
                        )
                    )
        return ParsedBatch(provenance=(provenance,), facts=tuple(facts))
