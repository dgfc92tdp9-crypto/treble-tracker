"""`DDIS` — an issuer's debt distribution by maturity (spec §7.5).

A maturity ladder: how much of an issuer's debt comes due, and when.

**This is a sample, not a census, and the difference is not cosmetic.**
Bloomberg's DDIS shows *amount outstanding* — what the issuer actually owes.
This one is built from N-PORT, which reports what *funds hold*, so it can
only show the part of an issuer's debt that appears in a filing this install
has ingested. An issuer with a large privately-placed programme, or one held
mainly outside registered funds, will look smaller here than it is.

Worse, the held amount is not even a sum across funds. Multiple filers hold
the same bond — `isin:US29444U7000` carries balances from four separate
filings — and they all write to one subject, so a point-in-time read returns
whichever filing was known most recently (I2). **The face figure is one
fund's position in that bond.**

So the ladder leads with what survives all of that. A *bond count* per
bucket is reliable: whether an issuer has four bonds maturing inside three
years is a fact about the issuer, not about who happens to hold them. The
coupon is a property of the paper. The held face is shown because it is
worth something, and is named so it cannot be read as issue size.

The report date is chosen as the one carrying the most of this issuer's
bonds rather than the most recent. N-PORT coverage of a recent month is thin
until funds file, and "most recent" is reliably the sparsest — the same trap
that once returned a single Treasury-bill issuer curve while thirty-five
corporate ones sat a month back, and that made `SWPM`'s basis tab go blank
on a day when two days earlier it had nine nodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from treble.store.duck import DuckStore
from treble.tapi.issuer_curves import bond_rows

#: Only straight corporate debt. `nport:issuerCat` cannot do this job — a
#: securitisation vehicle *is* a corporation and filers duly classify it
#: CORP — so the asset category is what separates a bond from a CLO tranche.
#: On the live store this takes 460 debt holdings down by 171.
CORPORATE_DEBT = frozenset({"DBT"})

#: Ladder buckets, in years. The last is open-ended: a thirty-year bond and
#: a hundred-year bond are both "long" to anyone reading a maturity profile,
#: and a bucket per decade at the far end would be mostly empty rows.
BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("0-1y", 0.0, 1.0),
    ("1-3y", 1.0, 3.0),
    ("3-5y", 3.0, 5.0),
    ("5-7y", 5.0, 7.0),
    ("7-10y", 7.0, 10.0),
    ("10y+", 10.0, float("inf")),
)


class DebtDistributionUnavailableError(ValueError):
    """No debt to profile for this issuer.

    Raised rather than returning empty buckets. A ladder of six zeroes and
    "this issuer has no bonds in any filing we hold" are different claims,
    and the first reads as an issuer that has repaid everything.
    """


@dataclass(frozen=True)
class MaturityBucket:
    """One rung of the ladder."""

    label: str
    bonds: int
    #: Sum of the held positions, USD. Named `held_face` everywhere it
    #: surfaces, never `outstanding`: see the module docstring.
    held_face: float
    #: Percent, as N-PORT reports it — `nport:annualizedRt` ranges 0 to 12.5
    #: on this store with a median of 4.625, so it is a rate in percent and
    #: not a decimal. Named with its unit because the two differ by a factor
    #: of a hundred and both look plausible on a screen.
    #:
    #: Unweighted. Weighting by held face would weight by which fund
    #: happened to file, and that is not a property of the debt.
    mean_coupon_pct: float | None
    earliest: date | None
    latest: date | None


@dataclass(frozen=True)
class DebtDistribution:
    """An issuer's ladder, and what it is built from."""

    lei: str
    issuer_name: str | None
    report_date: date
    buckets: tuple[MaturityBucket, ...]
    currencies: tuple[tuple[str, int], ...]
    #: Bonds excluded, and why — a ladder that silently dropped a
    #: securitisation would be a narrower claim than it appeared.
    excluded: tuple[tuple[str, str], ...]

    @property
    def total_bonds(self) -> int:
        return sum(b.bonds for b in self.buckets)

    @property
    def total_held_face(self) -> float:
        return sum(b.held_face for b in self.buckets)


def _years(maturity: date, report: date) -> float:
    return (maturity - report).days / 365.25


def _bucket_for(years: float) -> str | None:
    for label, low, high in BUCKETS:
        if low <= years < high:
            return label
    return None


def debt_distribution(
    store: DuckStore, *, lei: str, as_of: datetime, report_date: date | None = None
) -> DebtDistribution:
    """The maturity ladder for one issuer, by LEI.

    Keyed on LEI rather than name because names are not identifiers: a
    filer writing "Barclays PLC" and another writing "BARCLAYS PLC" are the
    same issuer and would fit two curves and two ladders.
    """
    rows = bond_rows(store, as_of=as_of)
    target = lei.strip().upper()

    # Registry over filer, as the issuer curves do: N-PORT's LEI is what a
    # fund administrator believed, GLEIF's is the issuer's registration, and
    # they disagree on 1.3% of bonds — clustered on subsidiaries attributed
    # to a parent, which is exactly the error that would put another
    # company's bonds on this ladder.
    mine = [
        row
        for row in rows
        if str(row.get("gleif:lei") or row.get("nport:lei") or "").upper() == target
    ]
    if not mine:
        raise DebtDistributionUnavailableError(
            f"no bonds for {target} in any N-PORT filing held here. This ladder is built "
            "from what funds report holding, so an issuer absent from every filing is "
            "invisible to it rather than debt-free"
        )

    # Sort each report date into usable bonds and exclusions *before*
    # choosing a date. The first version chose the date with the most
    # holdings and then filtered, which picked a day whose seven holdings
    # were every one of them a derivative — the ladder came back empty for
    # an issuer with six bonds a quarter earlier. Choosing by a count that
    # is not the count that matters is the same defect as `SWPM`'s basis
    # tab picking the newest day the *pair* built on when it needed three
    # curves.
    usable_by_day: dict[date, list[tuple[float, dict[str, object]]]] = {}
    excluded_by_day: dict[date, list[tuple[str, str]]] = {}
    for row in mine:
        day = row.get("report_date")
        if not isinstance(day, date):
            continue
        usable_by_day.setdefault(day, [])
        excluded_by_day.setdefault(day, [])
        identifier = str(row.get("identifier"))
        asset = row.get("nport:assetCat")
        if asset not in CORPORATE_DEBT:
            excluded_by_day[day].append(
                (identifier, f"asset category {asset!r}, not straight debt")
            )
            continue
        maturity = row.get("nport:maturityDt")
        if not isinstance(maturity, date):
            excluded_by_day[day].append((identifier, "no maturity reported"))
            continue
        years = _years(maturity, day)
        if years < 0:
            # Matured before the report date. Excluded and counted: a bond
            # past its maturity in a forward profile is either a stale
            # holding record or a default, and both are worth seeing rather
            # than being bucketed into "0-1y".
            excluded_by_day[day].append(
                (identifier, f"matured {maturity.isoformat()}, before the report")
            )
            continue
        usable_by_day[day].append((years, row))

    if report_date is not None:
        if report_date not in usable_by_day:
            raise DebtDistributionUnavailableError(
                f"{target} has no holdings reported on {report_date}"
            )
        chosen = report_date
    else:
        # Most *usable* bonds, not most holdings and not most recent.
        chosen = max(usable_by_day, key=lambda d: (len(usable_by_day[d]), d))

    kept = usable_by_day[chosen]
    excluded = excluded_by_day[chosen]
    currencies: dict[str, int] = {}
    for _, row in kept:
        currency = row.get("nport:curCd")
        if isinstance(currency, str):
            currencies[currency] = currencies.get(currency, 0) + 1

    if not kept:
        raise DebtDistributionUnavailableError(
            f"{target} appears on {len(usable_by_day)} report date(s) and none carries a "
            f"straight-debt holding with a future maturity ({len(excluded)} excluded on "
            f"{chosen}). Derivatives and securitisations are not a maturity ladder"
        )

    buckets: list[MaturityBucket] = []
    for label, _, _ in BUCKETS:
        members = [(y, r) for y, r in kept if _bucket_for(y) == label]
        coupons: list[float] = [
            float(value)
            for _, r in members
            if isinstance(value := r.get("nport:annualizedRt"), float | int)
        ]
        faces: list[float] = [
            float(value)
            for _, r in members
            if isinstance(value := r.get("nport:valUSD"), float | int)
        ]
        maturities: list[date] = [
            value for _, r in members if isinstance(value := r.get("nport:maturityDt"), date)
        ]
        buckets.append(
            MaturityBucket(
                label=label,
                bonds=len(members),
                held_face=sum(faces),
                mean_coupon_pct=(sum(coupons) / len(coupons)) if coupons else None,
                earliest=min(maturities) if maturities else None,
                latest=max(maturities) if maturities else None,
            )
        )

    names = [r.get("nport:name") for _, r in kept if isinstance(r.get("nport:name"), str)]
    return DebtDistribution(
        lei=target,
        issuer_name=str(names[0]) if names else None,
        report_date=chosen,
        buckets=tuple(buckets),
        currencies=tuple(sorted(currencies.items(), key=lambda pair: (-pair[1], pair[0]))),
        excluded=tuple(excluded),
    )


__all__ = [
    "BUCKETS",
    "CORPORATE_DEBT",
    "DebtDistribution",
    "DebtDistributionUnavailableError",
    "MaturityBucket",
    "debt_distribution",
]
