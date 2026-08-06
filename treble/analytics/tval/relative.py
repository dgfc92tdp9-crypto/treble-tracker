"""TVAL Prong 2 — relative value against an issuer's own curve (spec §15.1).

Prong 1 (`evaluate.py`) scores an evaluated price from observations of *that*
bond. Prong 2 asks a different question: given what the issuer's other bonds
are worth, is this one rich or cheap? A bond with no observations of its own
still has an answer, and a bond with observations gets a second opinion that
shares no inputs with the first.

**This was recorded as blocked, and the record was wider than the
measurement.** The note said §15.1 needs a similarity metric over sector,
rating and seniority, and that the store has none of the last two. Both
halves are true. What did not follow is that Prong 2 cannot be built: its
other half is *an issuer curve fitted across an issuer's outstanding debt*,
which needs issuer, maturity and price — all of which are present. Measured
2026-08-06: 328 bonds carry maturity, coupon, LEI and an implied mark, and
36 (issuer, report date) groups hold three or more.

**What is missing is named rather than approximated.** The comparable set
here matches on currency, issuer category and maturity proximity. It does
*not* match on rating or seniority, because the store holds neither, and a
similarity metric that quietly dropped two of its three dimensions would
return confident comparisons between a senior secured note and a
subordinated one. :class:`Comparables` carries the dimensions it actually
used, so a caller can see what the comparison is worth.

**A straight line, not a curve family.** Three to seven bonds cannot support
Nelson-Siegel: fitting four parameters to four points interpolates noise and
reports it as term structure. The fit is linear in time to maturity, and
`ComparableSet.slope_bp_per_year` is reported so a caller can see the shape
being assumed rather than infer it.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from treble.analytics.registry import model

#: The fewest bonds an issuer curve may be fitted through. Two points define
#: a line exactly and leave no residual, so every bond would price exactly on
#: its own curve and be reported as fairly valued — a rich/cheap engine that
#: can never find anything. Three is the fewest that can disagree.
MIN_CURVE_BONDS = 3

#: How far apart two maturities may be and still be comparable, in years.
#: Beyond this the curve is doing the work rather than the comparison.
DEFAULT_MATURITY_WINDOW = 3.0

#: The narrowest span of maturities a slope may be fitted across, in years.
#: Guarding on `variance == 0` is not enough and was a real defect: three
#: bonds sharing a maturity give a variance of ~1e-32 rather than 0, so the
#: exact comparison passed and the fit divided one denormal by another. The
#: slope it returned was arbitrary. A span this short cannot describe a term
#: structure anyway — the slope is the mark noise divided by a few weeks.
MIN_MATURITY_SPAN_YEARS = 0.25

#: Residual beyond which a bond is called rich or cheap rather than fair.
#: Named because it is a judgement: below it the difference is within what a
#: month-end fund mark can differ from a traded level for reasons that are
#: not value.
FAIR_BAND_BP = 25.0


@dataclass(frozen=True)
class IssuerBond:
    """One of an issuer's outstanding bonds, as the curve sees it."""

    identifier: str
    maturity: date
    yield_: float
    currency: str
    issuer_category: str | None = None

    def years_to(self, as_of: date) -> float:
        return (self.maturity - as_of).days / 365.25


@dataclass(frozen=True)
class IssuerCurve:
    """A line through an issuer's yields, and what it was fitted on."""

    issuer: str
    as_of: date
    intercept: float
    slope: float
    bonds: tuple[str, ...]
    #: Root-mean-square residual in basis points. The honest measure of how
    #: much the curve is worth: a curve with a 200bp RMS is not describing a
    #: term structure, and a rich/cheap call against it means little.
    residual_rms_bp: float

    def yield_at(self, years: float) -> float:
        return self.intercept + self.slope * years

    @property
    def slope_bp_per_year(self) -> float:
        return self.slope * 1e4


class InsufficientBondsError(ValueError):
    """Too few bonds to fit a curve, with the count.

    Raised rather than returning a flat curve at the average yield: a flat
    curve produces a confident-looking rich/cheap number in which every
    difference in maturity is read as a difference in value.
    """


@model(
    model_id="tval.issuer_curve",
    version="1.0",
    spec_section="§15.1",
    summary="Least-squares yield curve through an issuer's outstanding bonds",
)
def fit_issuer_curve(issuer: str, bonds: Sequence[IssuerBond], *, as_of: date) -> IssuerCurve:
    """Fit yield against time to maturity across one issuer's debt.

    Every bond must be observed on the same date. Mixing report dates fits a
    curve whose front end is March's and whose long end is May's — smooth,
    plausible and wrong, which is the failure `SWPM` refuses on its swap
    curve for the same reason.
    """
    if len(bonds) < MIN_CURVE_BONDS:
        raise InsufficientBondsError(
            f"{issuer} has {len(bonds)} usable bond(s); {MIN_CURVE_BONDS} is the fewest a "
            "curve can be fitted through. Two points fit a line exactly, leaving no residual, "
            "so every bond would be reported as fairly valued against itself"
        )
    currencies = {bond.currency for bond in bonds}
    if len(currencies) > 1:
        raise ValueError(
            f"{issuer}: bonds in {sorted(currencies)} cannot share a curve; a yield "
            "difference between currencies is the rate differential, not credit"
        )

    years = [bond.years_to(as_of) for bond in bonds]
    if min(years) <= 0:
        raise ValueError(
            f"{issuer}: a bond matures on or before {as_of}; a matured bond has no yield "
            "to fit and would anchor the front of the curve"
        )
    yields = [bond.yield_ for bond in bonds]

    mean_x = statistics.fmean(years)
    mean_y = statistics.fmean(yields)
    span = max(years) - min(years)
    if span < MIN_MATURITY_SPAN_YEARS:
        raise ValueError(
            f"{issuer}: the bonds span {span:.3f} years of maturity, under the "
            f"{MIN_MATURITY_SPAN_YEARS} required for a term structure. There is a level "
            "here but no slope, and dividing by a span this small returns mark noise "
            "amplified into a curve"
        )
    variance = sum((x - mean_x) ** 2 for x in years)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(years, yields, strict=True)) / variance
    intercept = mean_y - slope * mean_x

    residuals = [y - (intercept + slope * x) for x, y in zip(years, yields, strict=True)]
    rms = math.sqrt(sum(r**2 for r in residuals) / len(residuals)) * 1e4

    return IssuerCurve(
        issuer=issuer,
        as_of=as_of,
        intercept=intercept,
        slope=slope,
        bonds=tuple(bond.identifier for bond in bonds),
        residual_rms_bp=rms,
    )


@dataclass(frozen=True)
class RelativeValue:
    """Where one bond sits against its issuer's curve."""

    identifier: str
    issuer: str
    observed_yield: float
    curve_yield: float
    #: Positive means the bond yields more than the curve — cheap.
    residual_bp: float
    verdict: str
    #: The curve's own dispersion, carried alongside so a residual is never
    #: read without the noise it sits in. A 30bp call against a curve with a
    #: 90bp RMS is not a signal.
    curve_residual_rms_bp: float

    @property
    def is_significant(self) -> bool:
        """Whether the residual exceeds the curve's own scatter.

        A rich/cheap call smaller than the fit's RMS is inside the noise, and
        presenting it as a view would be reporting the fit's error as value.
        """
        return abs(self.residual_bp) > max(self.curve_residual_rms_bp, FAIR_BAND_BP)


@model(
    model_id="tval.relative_value",
    version="1.0",
    spec_section="§15.1",
    summary="A bond's yield residual against its own issuer's fitted curve",
)
def relative_value(bond: IssuerBond, curve: IssuerCurve) -> RelativeValue:
    """Rich/cheap against the issuer curve, with the curve's noise attached."""
    years = bond.years_to(curve.as_of)
    if years <= 0:
        raise ValueError(
            f"{bond.identifier} matures on or before the curve date {curve.as_of}; "
            "there is no yield to compare"
        )
    fitted = curve.yield_at(years)
    residual_bp = (bond.yield_ - fitted) * 1e4
    band = max(curve.residual_rms_bp, FAIR_BAND_BP)
    if residual_bp > band:
        verdict = "cheap"
    elif residual_bp < -band:
        verdict = "rich"
    else:
        verdict = "fair"
    return RelativeValue(
        identifier=bond.identifier,
        issuer=curve.issuer,
        observed_yield=bond.yield_,
        curve_yield=fitted,
        residual_bp=residual_bp,
        verdict=verdict,
        curve_residual_rms_bp=curve.residual_rms_bp,
    )


@dataclass(frozen=True)
class ComparableSet:
    """Bonds judged comparable, and on which dimensions.

    `dimensions` is the point of this type. §15.1 asks for similarity over
    sector, rating and seniority; this store holds none of the three, so the
    match is made on what exists and says so. A comparable set that reported
    only its members would let a caller believe a subordinated note had been
    excluded when nothing had looked.
    """

    target: str
    members: tuple[str, ...]
    dimensions: tuple[str, ...]
    missing_dimensions: tuple[str, ...]

    @property
    def is_complete(self) -> bool:
        """False whenever §15.1's full similarity metric could not be applied."""
        return not self.missing_dimensions

    @classmethod
    def around(
        cls,
        target: IssuerBond,
        universe: Sequence[IssuerBond],
        *,
        as_of: date,
        maturity_window: float = DEFAULT_MATURITY_WINDOW,
    ) -> ComparableSet:
        """Bonds close enough to the target to be compared with it.

        A constructor rather than a free function, for the reason
        `ReturnPanel.aligned` is one: the I3 registry walk requires every
        public callable in `treble.analytics` to carry a model envelope, and
        this selects inputs rather than producing a number anyone reads.
        Stamping a model id on a selection would put model identity on the
        choice of comparables instead of on the comparison.
        """
        target_years = target.years_to(as_of)
        members = tuple(
            bond.identifier
            for bond in universe
            if bond.identifier != target.identifier
            and bond.currency == target.currency
            and bond.issuer_category == target.issuer_category
            and abs(bond.years_to(as_of) - target_years) <= maturity_window
        )
        return cls(
            target=target.identifier,
            members=members,
            dimensions=AVAILABLE_DIMENSIONS,
            missing_dimensions=REQUIRED_DIMENSIONS,
        )


#: The dimensions §15.1 names, and what the store can currently match on.
#: Kept as data rather than prose so `missing_dimensions` is derived from one
#: place — a list that drifted from the code would understate what is absent.
REQUIRED_DIMENSIONS: tuple[str, ...] = ("sector", "rating", "seniority")
AVAILABLE_DIMENSIONS: tuple[str, ...] = ("currency", "issuer_category", "maturity_window")


__all__ = [
    "AVAILABLE_DIMENSIONS",
    "DEFAULT_MATURITY_WINDOW",
    "FAIR_BAND_BP",
    "MIN_CURVE_BONDS",
    "MIN_MATURITY_SPAN_YEARS",
    "REQUIRED_DIMENSIONS",
    "ComparableSet",
    "InsufficientBondsError",
    "IssuerBond",
    "IssuerCurve",
    "RelativeValue",
    "fit_issuer_curve",
    "relative_value",
]
