"""Issuer curves for `TVAL` Prong 2, assembled from stored N-PORT marks (§15.1).

Turns `isin:*` bond facts into per-issuer yield curves and rich/cheap calls.

**Where the prices come from, and what they are.** N-PORT reports a fund's
holdings: a value in USD and a face balance. Their ratio is an *implied mark*
— what one fund's valuation agent thought the bond was worth at a month end.
It is not a traded level and not a quote, and the screen says so. Two funds
holding the same bond can mark it differently, which is a real signal about
the bond and a real problem for a curve fitted through both.

**Three refusals, each of which would otherwise fit a plausible curve:**

1. *One report date per curve.* A curve whose front end is March's mark and
   whose long end is May's is smooth and wrong — the same refusal
   `SWPM` makes on its swap curve.
2. *One currency per curve.* A yield difference across currencies is the
   rate differential, not the issuer's credit.
3. *A yield that solved.* A mark implying a negative or absurd yield is
   dropped with its reason rather than fitted, because one bad point moves
   a three-point line further than it moves a thirty-point one.

Lives in `tapi` because reading the store is TAPI's job (I7); the fitting is
`analytics`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from treble.analytics._ql import DayCount, Market
from treble.analytics.bonds.pricing import yield_from_price
from treble.analytics.bonds.spec import FixedBondSpec, Frequency
from treble.analytics.tval.relative import (
    MIN_CURVE_BONDS,
    InsufficientBondsError,
    IssuerBond,
    IssuerCurve,
    RelativeValue,
    fit_issuer_curve,
    relative_value,
)
from treble.core.identifiers import TUID
from treble.store.duck import DuckStore

#: Yields outside this band are treated as a bad mark rather than a bond.
#: A fund reporting a stale balance produces an implied price of 3 or 3,000,
#: and the resulting yield is arithmetically fine and financially nonsense.
MIN_YIELD = -0.05
MAX_YIELD = 0.50

#: N-PORT gives no coupon frequency or day count. Semiannual 30/360 is the
#: US corporate convention and is *assumed*, not read — which changes a yield
#: by a few basis points and is stated on the screen rather than hidden here.
ASSUMED_FREQUENCY = Frequency.SEMIANNUAL
ASSUMED_DAY_COUNT = DayCount.THIRTY_360


class IssuerCurvesUnavailableError(RuntimeError):
    """No issuer has enough usable bonds, with the counts that led there."""


@dataclass(frozen=True)
class IssuerCurveSet:
    """Every issuer curve that could be fitted on one report date."""

    report_date: date
    curves: dict[str, IssuerCurve]
    bonds: dict[str, tuple[IssuerBond, ...]]
    #: Bonds read but not fitted, with the reason. Carried rather than
    #: dropped: a curve built from four of an issuer's nine bonds is a
    #: different object from one built on all nine, and the difference is
    #: invisible unless it is reported.
    excluded: tuple[tuple[str, str], ...]
    #: (report date, fittable issuers) for every date in the store.
    #: Carried so the chosen date is visibly a choice: N-PORT coverage of
    #: a recent month is thin until its funds have filed, and a screen
    #: showing one curve without showing that thirty-five were available a
    #: month earlier would read as 'this issuer is all there is'.
    coverage: tuple[tuple[date, int], ...]

    @property
    def issuers(self) -> tuple[str, ...]:
        return tuple(sorted(self.curves))

    def values_for(self, issuer: str) -> tuple[RelativeValue, ...]:
        curve = self.curves[issuer]
        return tuple(
            relative_value.__wrapped__(bond, curve)  # type: ignore[attr-defined]
            for bond in self.bonds[issuer]
        )


def _bond_rows(store: DuckStore, *, as_of: datetime) -> list[dict[str, object]]:
    """Every `isin:` bond with the fields a curve needs, point-in-time."""
    wanted = (
        "nport:lei",
        "nport:maturityDt",
        "nport:annualizedRt",
        "nport:curCd",
        "nport:valUSD",
        "nport:balance",
        "nport:issuerCat",
        "nport:name",
    )
    rows: list[dict[str, object]] = []
    for subject in store.subjects_with_prefix("isin:", as_of=as_of):
        facts = store.subject_facts(TUID(str(subject)), as_of=as_of)
        by_date: dict[date, dict[str, object]] = defaultdict(dict)
        for fact in facts:
            if fact.field in wanted:
                by_date[fact.effective_from][fact.field] = fact.value
        for day, values in by_date.items():
            rows.append({"identifier": str(subject), "report_date": day, **values})
    return rows


def build_issuer_curves(
    store: DuckStore, *, as_of: datetime, report_date: date | None = None
) -> IssuerCurveSet:
    """Fit a curve per issuer on a single report date.

    `report_date` defaults to the date with the **most fittable issuers**,
    not the most recent one. Measured 2026-08-06 on the live store:
    2026-03-31 fits 35 issuers, 2026-04-30 fits 1, and 2026-05-31 fits none.

    That ordering is the N-PORT filing cycle rather than the market. Funds
    file on staggered schedules, so the newest month end is always the least
    covered and fills in over the following weeks. Defaulting to it would
    hand back the sparsest sample every time — here a single US Treasury bill
    curve, while thirty-five corporate curves sat one month back. Never
    blended: a curve mixing March's front end with May's long end is smooth
    and wrong.
    """
    rows = _bond_rows(store, as_of=as_of)
    if not rows:
        raise IssuerCurvesUnavailableError(
            "no `isin:` bonds in the store; N-PORT holdings supply the marks these "
            "curves are fitted through"
        )

    excluded: list[tuple[str, str]] = []
    usable: list[tuple[date, IssuerBond, str]] = []
    for row in rows:
        identifier = str(row["identifier"])
        day = row["report_date"]
        if not isinstance(day, date):
            excluded.append((identifier, "no report date on the holding record"))
            continue
        maturity, coupon = row.get("nport:maturityDt"), row.get("nport:annualizedRt")
        lei, currency = row.get("nport:lei"), row.get("nport:curCd")
        value, balance = row.get("nport:valUSD"), row.get("nport:balance")
        if not isinstance(maturity, date) or not isinstance(coupon, float | int):
            excluded.append((identifier, "no maturity or coupon reported"))
            continue
        if not isinstance(lei, str) or not isinstance(currency, str):
            excluded.append((identifier, "no issuer LEI or currency reported"))
            continue
        if (
            not isinstance(value, float | int)
            or not isinstance(balance, float | int)
            or not balance
        ):
            excluded.append((identifier, "no value or face balance to imply a mark from"))
            continue
        if maturity <= day:
            excluded.append((identifier, f"matured on {maturity}, before the {day} report"))
            continue

        price = float(value) / float(balance) * 100.0
        spec = FixedBondSpec(
            coupon=float(coupon) / 100.0,
            # N-PORT does not report the issue date. The report date stands in:
            # it only sets the accrual origin for the schedule, and every bond
            # here is priced clean, so the yield is unaffected beyond the stub.
            issue_date=day,
            maturity=maturity,
            frequency=ASSUMED_FREQUENCY,
            day_count=ASSUMED_DAY_COUNT,
            calendar=Market.US_SETTLEMENT,
            settlement_days=0,
        )
        try:
            solved = yield_from_price.__wrapped__(spec, price, as_of=day)  # type: ignore[attr-defined]
        except Exception:
            # The reason recorded is the mark, not the exception type: a
            # price no yield solves for is a bad mark, and naming the price
            # is what lets someone check it against the filing.
            excluded.append((identifier, f"yield did not solve from a mark of {price:.2f}"))
            continue
        if not MIN_YIELD <= solved <= MAX_YIELD:
            excluded.append(
                (identifier, f"implied yield {solved * 100:.1f}% from a mark of {price:.2f}")
            )
            continue

        category = row.get("nport:issuerCat")
        usable.append(
            (
                day,
                IssuerBond(
                    identifier=identifier,
                    maturity=maturity,
                    yield_=solved,
                    currency=currency,
                    issuer_category=category if isinstance(category, str) else None,
                ),
                lei,
            )
        )

    if not usable:
        raise IssuerCurvesUnavailableError(
            f"none of the {len(rows)} bond records carried maturity, coupon, issuer and a "
            "mark that implied a plausible yield"
        )

    by_day: dict[date, dict[str, list[IssuerBond]]] = defaultdict(lambda: defaultdict(list))
    for day, bond, lei in usable:
        by_day[day][lei].append(bond)

    coverage = tuple(
        sorted(
            (day, sum(1 for bonds in issuers.values() if len(bonds) >= MIN_CURVE_BONDS))
            for day, issuers in by_day.items()
        )
    )
    if report_date is None:
        eligible = [(count, day) for day, count in coverage if count]
        if not eligible:
            most = max((len(b) for i in by_day.values() for b in i.values()), default=0)
            raise IssuerCurvesUnavailableError(
                f"no issuer has {MIN_CURVE_BONDS} bonds on any single report date; the most "
                f"on one date is {most}"
            )
        # Most issuers wins, most recent breaks the tie. See the docstring:
        # the newest month end is the least covered, not the most current.
        report_date = max(eligible)[1]

    chosen = by_day.get(report_date, {})
    curves: dict[str, IssuerCurve] = {}
    kept: dict[str, tuple[IssuerBond, ...]] = {}
    for lei, bonds in chosen.items():
        try:
            curves[lei] = fit_issuer_curve.__wrapped__(  # type: ignore[attr-defined]
                lei, bonds, as_of=report_date
            )
        except (InsufficientBondsError, ValueError) as error:
            excluded.append((lei, str(error).split(";")[0]))
            continue
        kept[lei] = tuple(sorted(bonds, key=lambda b: b.maturity))

    if not curves:
        raise IssuerCurvesUnavailableError(
            f"no issuer on {report_date} had {MIN_CURVE_BONDS} bonds a curve could be fitted "
            "through"
        )
    return IssuerCurveSet(
        report_date=report_date,
        curves=curves,
        bonds=kept,
        excluded=tuple(sorted(excluded)),
        coverage=coverage,
    )


__all__ = [
    "ASSUMED_DAY_COUNT",
    "ASSUMED_FREQUENCY",
    "MAX_YIELD",
    "MIN_YIELD",
    "IssuerCurveSet",
    "IssuerCurvesUnavailableError",
    "build_issuer_curves",
]
