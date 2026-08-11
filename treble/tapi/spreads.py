"""`SPRD` — a bond's spread over each benchmark (spec §10.1).

G-spread, I-spread and Z-spread were built and golden-tested in Phase 1 and
then sat unreachable, because every one of them needs a *curve* and there
was no curve to give them. `_WIRED_MODELS` in `tapi/local.py` still lists
five analytics; `bonds.z_spread`, `bonds.g_spread` and `bonds.oas` are not
among them, and the comment beside it says so plainly: "OAS needs a
bootstrapped curve and a vol assumption; until that exists, saying so is
better than a dash".

That is no longer true. The DTCC adapter builds swap curves and the Treasury
adapter now brings the CMT par curve in directly, so the missing input is
present and these three measures can finally be computed.

**The three spreads answer three different questions and are not
interchangeable.** A screen that showed one and labelled it "spread" would
be picking a benchmark on the reader's behalf:

* **G-spread** — over the *government* curve. The compensation for
  everything that is not a Treasury: credit, liquidity, and the swap spread
  along with it.
* **I-spread** — over the *swap* curve, read at the bond's maturity. The
  same idea against a bank-funding benchmark, so the swap spread drops out.
* **Z-spread** — the parallel shift to the *whole* zero curve that reprices
  the bond exactly. Unlike the other two it uses every cash flow rather than
  a single point, so it does not care about the shape between nodes.

G minus I is the swap spread at that maturity, and printing all three lets a
reader check that rather than take it on trust.

**What is here so far is the government curve the G-spread is measured
against**, which is the piece that did not exist. Building it exposed a
units error in `bonds.g_spread` itself — see that function — so the
correction landed before the screen that would have displayed the wrong
number. The per-bond rows and the `SPRD` screen are the next step; a
`BondSpreads` container was written for them and deleted again, because a
type nothing reads is the defect this repository keeps finding rather than
a head start on one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from treble.analytics.bonds.spec import FixedBondSpec
from treble.analytics.curves.bootstrap import Curve, CurveBuildError, build_curve
from treble.analytics.curves.config import (
    CurveConfig,
    InstrumentKind,
    InstrumentSpec,
)
from treble.core.identifiers import TUID
from treble.ingest.treasury_curve import CURVE as CMT_CURVE
from treble.ingest.treasury_curve import FIELD as CMT_FIELD
from treble.ingest.treasury_curve import TENORS as CMT_TENORS
from treble.store.duck import DuckStore

#: The CMT curve's own conventions. Treasury quotes constant-maturity par
#: yields on a semi-annual bond basis, so the bootstrap is told that rather
#: than left to assume the OIS conventions the swap curves use.
GOVT_DAY_COUNT = "ACT/ACT ICMA"
GOVT_CALENDAR = "us-government"
GOVT_FREQUENCY = 2

#: Tenors under a year are money-market, not par bonds. Treasury publishes
#: bills on a discount basis and quoting them as par swaps would misprice
#: the short end — where, for a bond with two years left, the G-spread is
#: read. Excluded rather than approximated.
MIN_GOVT_TENOR_YEARS = 1.0

#: A curve needs enough points to have a shape. Five is the same floor
#: `swap_market` uses, for the same reason.
MIN_GOVT_NODES = 5

_TENOR_YEARS: dict[str, float] = {
    "1M": 1 / 12,
    "6W": 0.115,
    "2M": 2 / 12,
    "3M": 0.25,
    "4M": 4 / 12,
    "6M": 0.5,
    "1Y": 1.0,
    "2Y": 2.0,
    "3Y": 3.0,
    "5Y": 5.0,
    "7Y": 7.0,
    "10Y": 10.0,
    "20Y": 20.0,
    "30Y": 30.0,
}


class GovtCurveUnavailableError(ValueError):
    """The CMT curve could not be built on any stored date."""


class BondNotPriceableError(ValueError):
    """The store holds nothing that can imply a price for this bond."""


@dataclass(frozen=True)
class BondSpread:
    """One bond measured against each benchmark, in basis points."""

    identifier: str
    issuer: str | None
    maturity: date
    coupon_pct: float
    price: float
    yield_pct: float
    g_spread_bp: float | None
    i_spread_bp: float | None
    z_spread_bp: float | None
    govt_date: date | None
    swap_date: date | None

    @property
    def swap_spread_bp(self) -> float | None:
        """G minus I: the swap spread at this maturity, derived not quoted.

        Shown because it is the arithmetic check on the other two. If the
        government and swap curves were built on different days, or one of
        them wrongly, this is where it surfaces — a swap spread of 300bp is
        not a market, it is a broken input.
        """
        if self.g_spread_bp is None or self.i_spread_bp is None:
            return None
        return self.g_spread_bp - self.i_spread_bp


def bond_spreads(store: DuckStore, *, identifier: str, as_of: datetime) -> BondSpread:
    """The three spreads for one bond, each against its own benchmark.

    **I-spread is computed by `g_spread` against the swap curve.** They are
    the same operation — a yield less a benchmark rate at the bond's
    maturity — and only the curve differs. Writing a second implementation
    would be writing a second chance to get the compounding conversion
    wrong, which is precisely the error that function was just fixed for.
    """
    from treble.analytics.bonds.pricing import g_spread, yield_from_price, z_spread
    from treble.tapi.issuer_curves import (
        ASSUMED_DAY_COUNT,
        ASSUMED_FREQUENCY,
        bond_rows,
    )
    from treble.tapi.swap_market import (
        SwapMarketUnavailableError,
        build_usd_discount_curve,
    )

    rows = [r for r in bond_rows(store, as_of=as_of) if str(r.get("identifier")) == identifier]
    if not rows:
        raise BondNotPriceableError(f"{identifier} has no N-PORT holding record")
    # The most recent report that carries everything needed, not simply the
    # most recent: a later filing missing a coupon is not an improvement.
    usable = [
        r
        for r in rows
        if isinstance(r.get("nport:maturityDt"), date)
        and isinstance(r.get("nport:annualizedRt"), float | int)
        and isinstance(r.get("nport:valUSD"), float | int)
        and isinstance(r.get("nport:balance"), float | int)
        and r.get("nport:balance")
    ]
    if not usable:
        raise BondNotPriceableError(
            f"{identifier} has no report carrying a maturity, coupon, value and face "
            "balance together — an implied mark needs all four"
        )
    row = max(usable, key=lambda r: r["report_date"])  # type: ignore[arg-type,return-value]
    day = row["report_date"]
    maturity = row["nport:maturityDt"]
    assert isinstance(day, date) and isinstance(maturity, date)  # noqa: S101 - narrowed above
    if maturity <= day:
        raise BondNotPriceableError(f"{identifier} matured on {maturity}, before the {day} report")
    currency = row.get("nport:curCd")
    if currency != "USD":
        # Both benchmarks here are USD — the CMT curve and the SOFR OIS
        # curve. Measuring an AUD bond against them produces three numbers
        # that all compute cleanly and mean nothing: the first bond this
        # function ever ran on was an Australian 2055 line, and it returned
        # a G-spread of +419bp against a curve from another country.
        raise BondNotPriceableError(
            f"{identifier} is denominated in {currency}; the benchmarks here are USD and "
            "a spread across currencies is not a spread"
        )

    coupon_pct = float(row["nport:annualizedRt"])  # type: ignore[arg-type]
    price = float(row["nport:valUSD"]) / float(row["nport:balance"]) * 100.0  # type: ignore[arg-type]
    spec = FixedBondSpec(
        coupon=coupon_pct / 100.0,
        issue_date=day,
        maturity=maturity,
        frequency=ASSUMED_FREQUENCY,
        day_count=ASSUMED_DAY_COUNT,
    )
    quoted_yield = yield_from_price.__wrapped__(spec, price, as_of=day)  # type: ignore[attr-defined]

    govt: float | None = None
    govt_date: date | None = None
    try:
        govt_curve, govt_date = build_govt_curve(store, as_of=as_of)
        govt = g_spread.__wrapped__(spec, price, govt_curve, as_of=day) * 10_000.0  # type: ignore[attr-defined]
    except (GovtCurveUnavailableError, CurveBuildError):
        # Left as None, never zero. A spread of zero is a bond trading on
        # the benchmark, which is a finding; an absent curve is not.
        govt = None

    i_spread: float | None = None
    z: float | None = None
    swap_date: date | None = None
    for candidate in _swap_curve_dates(store, as_of=as_of):
        try:
            swap_curve = build_usd_discount_curve(store, as_of=as_of, report_date=candidate)
        except (SwapMarketUnavailableError, CurveBuildError):
            continue
        swap_date = candidate
        # The same function as the G-spread, against the swap curve. See
        # the docstring: one implementation, one conversion.
        i_spread = g_spread.__wrapped__(spec, price, swap_curve, as_of=day) * 10_000.0  # type: ignore[attr-defined]
        try:
            z = z_spread.__wrapped__(spec, price, swap_curve, as_of=day) * 10_000.0  # type: ignore[attr-defined]
        except ValueError:
            # The solver brackets between -500bp and +5,000bp. A mark
            # outside that is a distressed price or a stale balance, not a
            # spread — reported as absent rather than clamped to the
            # bracket, which would put a bond at exactly +5,000bp and look
            # like a measurement.
            z = None
        break

    name = row.get("nport:name")
    return BondSpread(
        identifier=identifier,
        issuer=str(name) if isinstance(name, str) else None,
        maturity=maturity,
        coupon_pct=coupon_pct,
        price=price,
        yield_pct=quoted_yield * 100.0,
        g_spread_bp=govt,
        i_spread_bp=i_spread,
        z_spread_bp=z,
        govt_date=govt_date if govt is not None else None,
        swap_date=swap_date,
    )


def _swap_curve_dates(store: DuckStore, *, as_of: datetime) -> list[date]:
    """USD curve dates, newest first. Newest *usable* is chosen by trial."""
    from treble.tapi.swap_market import USD_DISCOUNT_CURVE

    days: set[date] = set()
    for subject in store.subjects_with_prefix(f"swap:{USD_DISCOUNT_CURVE}", as_of=as_of):
        for fact in store.read(TUID(str(subject)), "PAR_RATE", as_of=as_of):
            days.add(fact.effective_from)
    return sorted(days, reverse=True)


def govt_curve_dates(store: DuckStore, *, as_of: datetime) -> list[date]:
    """Report dates with enough CMT points to bootstrap, newest first."""
    by_day: dict[date, int] = {}
    for tenor in CMT_TENORS.values():
        if _TENOR_YEARS.get(tenor, 0.0) < MIN_GOVT_TENOR_YEARS:
            continue
        for fact in store.read(TUID(f"govt:{CMT_CURVE}:{tenor}"), CMT_FIELD, as_of=as_of):
            if isinstance(fact.value, float | int):
                by_day[fact.effective_from] = by_day.get(fact.effective_from, 0) + 1
    return sorted((d for d, n in by_day.items() if n >= MIN_GOVT_NODES), reverse=True)


def build_govt_curve(
    store: DuckStore, *, as_of: datetime, report_date: date | None = None
) -> tuple[Curve, date]:
    """Bootstrap the CMT par curve, newest usable day first.

    Walks days rather than taking the newest, because the newest day is
    reliably the thinnest — the trap that emptied `SWPM`'s basis tab and
    `DDIS`'s ladder. A day that fails to bootstrap is skipped and the next
    tried, and only an exhausted list is an error.
    """
    days = govt_curve_dates(store, as_of=as_of)
    if report_date is not None:
        days = [report_date] if report_date in days else []
    if not days:
        raise GovtCurveUnavailableError(
            f"no day carries {MIN_GOVT_NODES} CMT tenors of a year or more. The bills are "
            "quoted on a discount basis and are excluded rather than approximated as par "
            "bonds, so a store holding only the short end cannot build this curve"
        )
    failures: list[str] = []
    for day in days:
        quotes: dict[tuple[InstrumentKind, str], float] = {}
        for tenor in CMT_TENORS.values():
            if _TENOR_YEARS.get(tenor, 0.0) < MIN_GOVT_TENOR_YEARS:
                continue
            facts = [
                f
                for f in store.read(TUID(f"govt:{CMT_CURVE}:{tenor}"), CMT_FIELD, as_of=as_of)
                if f.effective_from == day and isinstance(f.value, float | int)
            ]
            for fact in facts[:1]:
                if isinstance(fact.value, float | int):
                    quotes[(InstrumentKind.SWAP, tenor)] = float(fact.value)
        config = CurveConfig(
            name=CMT_CURVE,
            currency="USD",
            day_count=GOVT_DAY_COUNT,
            calendar=GOVT_CALENDAR,
            fixed_frequency=GOVT_FREQUENCY,
            instruments=tuple(
                InstrumentSpec(kind=kind, tenor=tenor) for kind, tenor in sorted(quotes)
            ),
        )
        try:
            return build_curve(config, quotes, as_of=day), day
        except CurveBuildError as error:  # a bad day is skipped, not fatal
            failures.append(f"{day}: {error}")
    raise GovtCurveUnavailableError(
        f"no CMT curve bootstrapped on {len(days)} candidate day(s): {'; '.join(failures[:3])}"
    )


__all__ = [
    "GOVT_CALENDAR",
    "GOVT_DAY_COUNT",
    "MIN_GOVT_NODES",
    "MIN_GOVT_TENOR_YEARS",
    "BondNotPriceableError",
    "BondSpread",
    "GovtCurveUnavailableError",
    "bond_spreads",
    "build_govt_curve",
    "govt_curve_dates",
]
