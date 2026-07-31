"""Prices implied by fund holdings disclosures (spec §9.5, §12).

Every registered fund files N-PORT reporting, per holding, the quantity held
and its US-dollar fair value. Their ratio is a price — sourced from an SEC
filing rather than a vendor, which makes it the only price in this system
whose provenance is a primary regulatory document.

**Why this exists.** Free daily equity prices are not obtainable on terms
this project will accept: the options either require defeating a bot
challenge or breach a terms-of-service (recorded in PROGRESS). Holdings
disclosures are published, licence-free, and cover every security any
registered fund owns — which is most of the investable universe.

**What it is not.** These are period-end marks, not a daily series, and
they are the *filer's own* valuation under ASC 820 rather than a traded
price. A fund's fair value for an illiquid bond is an estimate, and N-PORT
does not state whether a debt holding's value includes accrued interest, so
an implied bond price sits somewhere between clean and dirty. None of that
is hidden: the marks are reported with their dispersion, and dispersion is
the honest measure of how much the filers agree.

**The property no vendor feed has.** When many funds independently value the
same security on the same date, agreement is evidence and disagreement is a
warning. A single vendor price cannot tell you how confident to be; a
distribution can. That is why the consensus model returns a spread and a
count alongside the number, and why the number alone is never enough.
"""

from __future__ import annotations

import statistics
from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict

from treble.analytics.registry import model


class AssetCategory(Enum):
    """N-PORT ``assetCat`` values this model can price.

    Deliberately closed: the quantity in ``balance`` means different things
    per category — shares for equity, par for debt — and guessing which
    would silently scale a price by a hundred.
    """

    EQUITY = "EC"
    DEBT = "DBT"


class ImpliedMark(BaseModel):
    """One filer's implied price for one security on one date."""

    model_config = ConfigDict(frozen=True)

    price: float
    #: The filer, so a consensus can be traced back to who valued what.
    filer: str
    #: The report date this mark describes. Required, because funds file on
    #: different period ends and a consensus that mixed them would measure
    #: time drift as though it were disagreement.
    as_of: date


class Consensus(BaseModel):
    """What a set of filers collectively imply, and how much they agree."""

    model_config = ConfigDict(frozen=True)

    price: float
    #: The single report date every contributing mark shares.
    as_of: date
    #: Number of independent filers contributing a mark.
    filers: int
    #: Half the interquartile range, in the same units as the price. Reported
    #: rather than hidden because a tight cluster and a wild scatter must not
    #: render identically.
    dispersion: float
    #: Highest and lowest mark, which is what a reader actually wants to see
    #: when dispersion is large.
    low: float
    high: float

    @property
    def dispersion_bps_of_price(self) -> float:
        """Dispersion relative to the price, for a scale-free comparison."""
        return 0.0 if self.price == 0 else abs(self.dispersion / self.price) * 10_000


class UnpriceableHoldingError(ValueError):
    """The disclosure cannot yield a price."""


@model(
    model_id="holdings.implied_price",
    version="1.0",
    spec_section="§9.5",
    summary="Price implied by a fund's reported quantity and fair value",
)
def implied_price(balance: float, val_usd: float, category: AssetCategory) -> float:
    """One filer's implied price from a single N-PORT holding line.

    Equity is quoted per share and debt per 100 of par, matching how each is
    conventionally shown — a debt holding's ``balance`` is a par amount, so
    the ratio must be scaled by 100 or a bond appears to trade near 0.97.
    """
    if balance == 0:
        # Not an edge case to smooth over: a zero quantity with a non-zero
        # value is a filing that cannot imply a price at all.
        raise UnpriceableHoldingError("balance is zero; no price is implied")
    # Short positions carry a negative quantity and a negative value. The
    # ratio is still the price, but only if both signs agree; mismatched
    # signs would return a negative price presented as fact.
    if balance < 0 and val_usd > 0:
        raise UnpriceableHoldingError("negative quantity with positive value")
    price = val_usd / balance
    return price * 100.0 if category is AssetCategory.DEBT else price


@model(
    model_id="holdings.consensus_price",
    version="1.0",
    spec_section="§9.5",
    summary="Median of independent filer marks, with their dispersion",
)
def consensus_price(marks: tuple[ImpliedMark, ...]) -> Consensus:
    """Combine independent filer marks into one price plus its spread.

    The median, not the mean: one fund fat-fingering a quantity by a factor
    of ten would drag a mean anywhere, and a wrong price that looks
    reasonable is the failure this project exists to avoid.

    A single mark is reported with zero dispersion and a filer count of one.
    That is not the same as agreement, and the count is what says so.
    """
    if not marks:
        raise UnpriceableHoldingError("no marks to combine")

    dates = {mark.as_of for mark in marks}
    if len(dates) > 1:
        # Refused rather than blended. Funds file on different period ends,
        # and combining across them makes the spread measure elapsed time
        # plus disagreement, with nothing in the number saying which. A
        # spread that conflates two causes is worse than no spread, because
        # a reader would act on it. Same rule ICVS applies to curve tenors.
        raise UnpriceableHoldingError(
            "marks span report dates "
            f"({', '.join(d.isoformat() for d in sorted(dates))}); "
            "group by date before combining"
        )

    prices = sorted(mark.price for mark in marks)
    median = statistics.median(prices)

    if len(prices) >= 4:
        quartiles = statistics.quantiles(prices, n=4, method="inclusive")
        dispersion = (quartiles[2] - quartiles[0]) / 2.0
    elif len(prices) > 1:
        # Too few points for quartiles to mean anything; the half-range is
        # the honest summary rather than a statistic implying more data.
        dispersion = (prices[-1] - prices[0]) / 2.0
    else:
        dispersion = 0.0

    return Consensus(
        price=median,
        as_of=dates.pop(),
        filers=len(prices),
        dispersion=dispersion,
        low=prices[0],
        high=prices[-1],
    )
