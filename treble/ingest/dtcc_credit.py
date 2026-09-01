"""Single-name CDS from the SEC security-based swap tape (spec §13, `CDSW`).

The pricer in `analytics.credit.cds` has been written and validated against
ISDA's published grids since Phase 2 began, and has sat unreachable because
the store held no credit subjects. That was recorded as blocked on data. It
was not:

    CFTC_CUMULATIVE_CREDIT_2026_08_28.zip    403
    SEC_CUMULATIVE_CREDITS_2026_08_28.zip    200

**Single-name CDS are security-based swaps.** They are reported to the SEC,
not the CFTC, so they are on a different endpoint under a different slug —
plural, and the earlier probe used neither. `dtcc.py` reads the CFTC rates
tape; this reads the SEC credit one, and they are separate files rather than
two views of one.

## What the tape gives, and what it does not

967 rows for 2026-08-28, 586 of them new trades, across 279 reference
entities. Coverage of the fields a curve needs, measured on that file:

| field | coverage | |
|---|---|---|
| `Underlier ID-Leg 1` (Markit RED) | 577/586 | the reference entity, properly identified |
| `Expiration Date`, `Effective Date` | 586/586 | tenor |
| `Fixed rate-Leg 1` | 537/586 | the standardised coupon, 100bp or 500bp |
| `Other payment amount` where type is `UFRO` | 279/586 | points upfront |
| `Spread-Leg 1` | **62/586** | the traded spread, rarely quoted |

That last row is the shape of the market rather than a gap in the file.
Standard CDS trade at a *fixed* coupon with an upfront payment, so the spread
is usually implied rather than quoted. This adapter records what the tape
says and does not invent the rest: a spread fact is written only where a
spread was quoted, and the upfront is carried as a fraction of notional so
the implied spread can be solved for by analytics that own a discount curve.

## Why the RED code is the key

`Underlier ID source-Leg 1` reads `REDID` — the Markit Reference Entity
Database code, which is what the market keys a reference entity on.
`Underlying Asset Name` is *also* present and is not usable as a key: the
same file carries both "Republic of Colombia" and "REPUBLIC OF COLOMBIA",
which would be two entities to anything keying on the string. The name is
carried as a fact *about* the RED code instead, which is where a label
belongs.

RED codes are Markit's, and the file is redistribution-restricted for the
same reason the rates tape is (§9.3, the CUSIP guard).
"""

from __future__ import annotations

import statistics
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime

from treble.core.facts import Fact
from treble.core.identifiers import TUID, looks_like_isin
from treble.core.provenance import ExtractionMethod, Provenance
from treble.ingest.base import ParsedBatch, RawPayload, SourceAdapter, SourceMeta
from treble.ingest.dtcc import (
    DtccParseError,
    _as_date,
    _decimal,
    _rows_from_zip,
    report_date_from_uri,
)
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore

#: The SEC security-based swap repository's daily cumulative credit file.
FILE_URL = (
    "https://pddata.dtcc.com/ppd/api/report/cumulative/sec/SEC_CUMULATIVE_CREDITS_{stamp}.zip"
)

#: ISO 20022 spread notation codes, and the factor that takes each to a
#: decimal fraction. Both appear in one file — 59 rows decimal, 3 basis
#: points on 2026-08-28 — so reading either as the other is a 10,000x error
#: on a number a screen shows.
SPREAD_NOTATION_TO_DECIMAL: dict[str, float] = {
    "3": 1.0,  # decimal fraction, e.g. 0.0163
    "4": 1e-4,  # basis points, e.g. 165
}

#: The `Other payment type` code for an upfront payment.
UPFRONT_PAYMENT_TYPE = "UFRO"

#: The tenors a CDS curve is quoted on.
STANDARD_TENORS: tuple[int, ...] = (1, 2, 3, 4, 5, 7, 10)

#: How far from a standard tenor a contract may sit and still be counted on
#: it, in years.
#:
#: Half a year, where `dtcc.py`'s rates curve allows 0.03 — and the
#: difference is the convention, not laxity. CDS mature on IMM dates (the
#: 20th of March, June, September, December), so a contract's time to
#: maturity is almost never a whole number: measured on 2026-08-28 the
#: dominant bucket is **4.81 years**, 180 prints, which is the 5Y point
#: maturing 2031-06-20. A 0.03 tolerance kept 2 prints out of 586.
TENOR_TOLERANCE_YEARS = 0.5

#: Prints below this on one tenor are omitted rather than averaged. A curve
#: point from a single trade is one dealer's axe, not a market level.
MIN_TRADES_PER_TENOR = 2

_DAYS_PER_YEAR = 365.25


#: Identifier sources the tape uses, best entity key first.
#:
#: Both columns are semicolon-separated lists that line up positionally —
#: `LEI;ISIN;REDID` against `F5WCU...;XS1410426024;FF667M` — so a row can
#: carry three identifiers for one contract.
#:
#: `REDID` first because a Markit RED code names the *reference entity*,
#: which is what a CDS curve is about. `LEI` names an entity too. An `ISIN`
#: names a reference **obligation** — a particular bond — and two bonds of
#: one issuer are two ISINs, so it is the weakest key here and still the
#: only one 447 of 586 prints carry.
ID_SOURCE_PREFERENCE: tuple[str, ...] = ("REDID", "LEI", "ISIN", "CUSIP")


def identifiers(row: dict[str, str]) -> dict[str, str]:
    """Every identifier a print carries, by source.

    Positional: the two lists are zipped, and a row whose lists differ in
    length yields nothing rather than a guessed pairing — mismatched lists
    mean the file's shape is not what this reads, and pairing them anyway
    would attach an entity's name to another entity's code.
    """
    sources = [s.strip().upper() for s in (row.get("Underlier ID source-Leg 1") or "").split(";")]
    values = [v.strip() for v in (row.get("Underlier ID-Leg 1") or "").split(";")]
    if len(sources) != len(values):
        return {}
    return {
        source: value
        for source, value in zip(sources, values, strict=True)
        if source and value and _identifier_is_real(source, value)
    }


def _identifier_is_real(source: str, value: str) -> bool:
    """Whether a value is an identifier at all, or a placeholder wearing one.

    The tape carries `XSSNRREFOBL0` in the ISIN column — "XS senior reference
    obligation", a stand-in used when the specific obligation is not being
    named. On 2026-08-28 it appears against **five different reference
    entities**: Volvo, Alstom, Deutsche Bank and two more. Keyed on it, they
    became one curve, and that curve priced Volkswagen at 1,475bp.

    An ISIN's check digit settles it without a denylist: `XSSNRREFOBL0` and
    `XSLACREFOBL0` both fail it and every real ISIN in the file passes. A
    placeholder invented next quarter is caught by the same rule, which a
    list of known bad strings would not be.

    Only ISINs are checked, and deliberately. A Markit RED code has no check
    digit to verify, so there is nothing to test it against — and inventing a
    shape rule for it would reject valid codes to catch a placeholder nobody
    has seen.
    """
    return looks_like_isin(value) if source == "ISIN" else True


def best_identifier(row: dict[str, str]) -> tuple[str, str] | None:
    """The strongest identifier a print carries, as (source, value)."""
    found = identifiers(row)
    for source in ID_SOURCE_PREFERENCE:
        if source in found:
            return source, found[source]
    return None


def reference_subject(source: str, value: str) -> TUID:
    """`REDID`, `FF667M` -> `cds:redid:FF667M` — the reference entity.

    Namespaced by identifier source, and deliberately **not** merged across
    sources. `cds:redid:FF667M` and `cds:isin:XS1410426024` may well be the
    same issuer — the tape says so on the four rows that carry both — but
    an ISIN alone does not establish it. Two subjects that turn out to be
    one entity can be linked later; one subject that silently merged two
    entities is a wrong number on a screen, and this repository has fixed
    enough of those.
    """
    return TUID(f"cds:{source.strip().lower()}:{value.strip().upper()}")


def curve_subject(source: str, value: str, tenor: str) -> TUID:
    """One point on a reference entity's curve."""
    return TUID(f"{reference_subject(source, value)}:{tenor}")


def spread_decimal(value: str | None, notation: str | None) -> float | None:
    """A quoted spread as a decimal fraction, or None if it is not usable.

    An unrecognised notation returns None rather than a guess. The two codes
    in the file differ by a factor of ten thousand, so assuming one would
    not produce a slightly wrong spread — it would produce a wrong one.
    """
    number = _decimal(value)
    if number is None or notation is None:
        return None
    factor = SPREAD_NOTATION_TO_DECIMAL.get(notation.strip())
    return None if factor is None else number * factor


def _tenor_years(row: dict[str, str]) -> int | None:
    effective, expiry = _as_date(row.get("Effective Date")), _as_date(row.get("Expiration Date"))
    if effective is None or expiry is None:
        return None
    years = (expiry - effective).days / _DAYS_PER_YEAR
    nearest = min(STANDARD_TENORS, key=lambda t: abs(years - t))
    if abs(years - nearest) > TENOR_TOLERANCE_YEARS:
        return None
    return nearest


def notional_is_capped(row: dict[str, str]) -> bool:
    """Whether the notional is a dissemination cap rather than a size.

    A large print is published as `5,000,000+` — "this much or more".
    CLAUDE.md §6 states the rule for TRACE and it holds here: **do not treat
    the cap as the actual size.** 275 of 586 prints on 2026-08-28 carry it.

    Detected from the trailing `+` and nothing else, because on this file
    nothing else says so: `Block trade election indicator` and `Large
    notional off-facility swap election indicator`, which `dtcc.py` reads on
    the rates tape, are **blank on all 586 rows**. An adapter that reused
    those columns here would find no capped trades and report every upper
    bound as a level.
    """
    return (row.get("Notional amount-Leg 1") or "").strip().endswith("+")


def _upfront_fraction(row: dict[str, str]) -> float | None:
    """Points upfront: the payment as a fraction of notional.

    ``None`` when the notional is capped, and that is the point. The
    payment is known exactly and the size only as a floor, so the quotient
    is an **upper bound** — a 915,667 payment on "5,000,000 or more" is *at
    most* 18.3 points, and would be 4.6 if the trade was 20 million. On the
    live file that upper bound reads as a distressed quote on Advanced Micro
    Devices, which is not what happened.

    Publishing a bound as a level is the mistake §6 exists to prevent, so
    198 of the 269 prints carrying an upfront contribute none. What they do
    contribute is `CAPPED_TRADE_COUNT`, so a thin curve point is visibly
    thin rather than quietly so.

    Carried as a fraction rather than an amount because the amount alone
    says nothing without the size it was paid on.
    """
    if (row.get("Other payment type") or "").strip().upper() != UPFRONT_PAYMENT_TYPE:
        return None
    if notional_is_capped(row):
        return None
    amount = _decimal(row.get("Other payment amount"))
    notional = _decimal(row.get("Notional amount-Leg 1"))
    if amount is None or notional is None or notional <= 0:
        return None
    return amount / notional


class CreditObservation:
    """One tenor of one reference entity, reduced from the day's prints."""

    __slots__ = (
        "capped",
        "coupon",
        "identifier",
        "source",
        "spread",
        "tenor",
        "trades",
        "upfront",
    )

    def __init__(
        self,
        *,
        source: str,
        identifier: str,
        tenor: int,
        trades: int,
        capped: int,
        spread: float | None,
        coupon: float | None,
        upfront: float | None,
    ) -> None:
        self.source = source
        self.identifier = identifier
        self.tenor = tenor
        self.trades = trades
        self.capped = capped
        self.spread = spread
        self.coupon = coupon
        self.upfront = upfront


def credit_observations(rows: Sequence[dict[str, str]], report: date) -> list[CreditObservation]:
    """Reduce a day's credit prints to curve points. Pure.

    The same lifecycle filter as the rates tape: new trades only, so an
    amendment cannot count a contract twice.
    """
    buckets: dict[tuple[str, str, int], list[dict[str, float | None]]] = {}
    for row in rows:
        if row.get("Action type") != "NEWT" or row.get("Event type") != "TRAD":
            continue
        identifier = best_identifier(row)
        if identifier is None:
            continue
        source, value = identifier
        executed = _as_date(row.get("Event timestamp"))
        if executed is not None and executed != report:
            # A print carried in this file but executed on another day
            # belongs to that day, exactly as in `dtcc.py`.
            continue
        tenor = _tenor_years(row)
        if tenor is None:
            continue
        buckets.setdefault((source, value, tenor), []).append(
            {
                "spread": spread_decimal(row.get("Spread-Leg 1"), row.get("Spread notation-Leg 1")),
                "coupon": _decimal(row.get("Fixed rate-Leg 1")),
                "upfront": _upfront_fraction(row),
                "capped": 1.0 if notional_is_capped(row) else 0.0,
            }
        )

    out: list[CreditObservation] = []
    for (source, value, tenor), prints in sorted(buckets.items()):
        if len(prints) < MIN_TRADES_PER_TENOR:
            continue

        def median_of(key: str, prints: list[dict[str, float | None]] = prints) -> float | None:
            values = [value for p in prints if (value := p[key]) is not None]
            return statistics.median(values) if values else None

        out.append(
            CreditObservation(
                source=source,
                identifier=value,
                tenor=tenor,
                trades=len(prints),
                capped=int(sum(p["capped"] or 0.0 for p in prints)),
                spread=median_of("spread"),
                coupon=median_of("coupon"),
                upfront=median_of("upfront"),
            )
        )
    return out


def entity_names(rows: Sequence[dict[str, str]]) -> dict[tuple[str, str], str]:
    """Identifier to the name the tape gives it, longest form winning.

    The same entity appears as "Republic of Colombia" and "REPUBLIC OF
    COLOMBIA". Neither is more correct, and preferring the one that has not
    been flattened to capitals gives a person something readable — keyed on
    the identifier, which is what actually distinguishes entities.
    """
    names: dict[tuple[str, str], str] = {}
    for row in rows:
        identifier = best_identifier(row)
        name = (row.get("Underlying Asset Name") or "").strip()
        if identifier is None or not name:
            continue
        current = names.get(identifier)
        if current is None or (name != name.upper() and current == current.upper()):
            names[identifier] = name
    return names


class DtccSdrCreditAdapter(SourceAdapter):
    """Daily SEC credit cumulative files, reduced to CDS curve points."""

    meta = SourceMeta(
        source_id="dtcc-credit",
        expected_cadence_days=1.0,
        description=(
            "DTCC SEC security-based swap repository, daily cumulative credit file "
            "— single-name CDS prints reduced to reference-entity curve points"
        ),
        licence=(
            "DTCC public price dissemination. Terms UNVERIFIED, and the underlier "
            "identifiers are Markit RED codes, so this is treated as restricted "
            "for the same reason the CUSIP guard exists (§9.3)."
        ),
        redistribution_restricted=True,
        rate_limit_per_second=0.2,
    )
    #: 2 — placeholder ISINs rejected. Version 1 keyed on `XSSNRREFOBL0`,
    #: which is not an identifier: it is "XS senior reference obligation",
    #: a stand-in the tape uses when the specific obligation is not named,
    #: and on 2026-08-28 it stood in for five different entities. Their
    #: prints became one curve, and that curve priced Volkswagen at
    #: 1,475bp. The check digit rejects it; every real ISIN in the file
    #: passes.
    parser_version = "2"

    def __init__(
        self,
        payloads: PayloadStore,
        log: IngestLog,
        *,
        report_dates: Sequence[date],
        timeout: float = 120.0,
    ) -> None:
        super().__init__(payloads, log)
        if not report_dates:
            raise ValueError("no report dates requested; an empty window fetches nothing")
        self._dates = tuple(report_dates)
        self._timeout = timeout

    def fetch(self) -> Iterator[RawPayload]:
        for report in self._dates:
            url = FILE_URL.format(stamp=report.strftime("%Y_%m_%d"))
            response = self._get(url, timeout=self._timeout, attempts=2)
            yield RawPayload(data=response.content, source_uri=url, fetched_at=_utcnow())

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        report = report_date_from_uri(payload.source_uri)
        rows = _rows_from_zip(payload.data)
        provenance = Provenance(
            source_system=self.meta.source_id,
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.API,
            extractor_version=self.parser_version,
            payload_hash=payload_hash,
        )
        names = entity_names(rows)
        facts: list[Fact] = []

        def add(subject: TUID, field: str, value: float | str) -> None:
            facts.append(
                Fact(
                    subject=subject,
                    field=field,
                    value=value,
                    effective_from=report,
                    effective_to=report,
                    # The file is published after the close of the day it
                    # describes, so the fetch is the earliest this could
                    # have been known — the same reasoning as the rates tape.
                    knowledge_from=payload.fetched_at,
                    provenance_id=provenance.id,
                )
            )

        seen: set[tuple[str, str]] = set()
        for observation in credit_observations(rows, report):
            key = (observation.source, observation.identifier)
            subject = curve_subject(
                observation.source, observation.identifier, f"{observation.tenor}Y"
            )
            add(subject, "TRADE_COUNT", float(observation.trades))
            if observation.capped:
                add(subject, "CAPPED_TRADE_COUNT", float(observation.capped))
            if observation.spread is not None:
                add(subject, "PAR_SPREAD", observation.spread)
            if observation.coupon is not None:
                add(subject, "CDS_COUPON", observation.coupon)
            if observation.upfront is not None:
                add(subject, "UPFRONT_FRACTION", observation.upfront)
            if key not in seen:
                seen.add(key)
                name = names.get(key)
                if name:
                    add(reference_subject(*key), "REFERENCE_ENTITY", name)
        return ParsedBatch(facts=facts, provenance=[provenance])


def _utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "FILE_URL",
    "MIN_TRADES_PER_TENOR",
    "SPREAD_NOTATION_TO_DECIMAL",
    "UPFRONT_PAYMENT_TYPE",
    "CreditObservation",
    "DtccParseError",
    "DtccSdrCreditAdapter",
    "credit_observations",
    "curve_subject",
    "entity_names",
    "notional_is_capped",
    "reference_subject",
    "spread_decimal",
]
