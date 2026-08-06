"""A swaption volatility surface from transacted prints (spec §11.3).

`swaption.py` turns one premium into one volatility. This turns many into a
grid — and the difference that matters is not the aggregation but what it
refuses to do.

**Nothing is interpolated.** A node exists where trades happened and does not
exist where they did not. An interpolated node is indistinguishable on screen
from an observed one, and a surface that quietly fills its holes reports
confidence it does not have. :meth:`VolSurface.at` returns `None` for an
empty node rather than the nearest neighbour.

**Every node carries its observation count.** A node backed by one print and
a node backed by twelve are different objects, and a grid that showed only
the number would make them look alike. `MIN_OBSERVATIONS_FOR_CONFIDENT` marks
which is which rather than dropping the thin ones — a single print is still
the only thing the market said about that point.

**Wing trades are excluded, with the reason on the surface.** Measured on the
live tape, prints outside 10% moneyness imply roughly three times the
at-the-money volatility in *both* Black and Bachelier terms, which is too
steep for a smile in either convention and is not explained. Including them
would smear an unexplained anomaly across the grid; excluding them silently
would hide that the surface covers only the middle. :class:`VolSurface`
reports how many were dropped and why.

**The median, not the mean.** These are executed trades at different moments
of one day against a daily curve. One crossed print at a stale level moves a
mean and does not move a median.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from treble.analytics.registry import model
from treble.analytics.vol.swaption import SwaptionQuote

#: How far from the money a print may sit and still be used. Beyond this the
#: tape's implied vols are unexplained (see the module docstring), and vega
#: is small enough that a premium error buys a large volatility.
DEFAULT_MONEYNESS_BAND = 0.10

#: Standard grid points, in years. A trade is assigned to the nearest, and a
#: trade further than `MAX_BUCKET_DRIFT` from any is dropped rather than
#: stretched onto one — a 4-year expiry called "5Y" is a wrong label on a
#: real number, which is worse than an absent one.
EXPIRY_BUCKETS: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0)
TENOR_BUCKETS: tuple[float, ...] = (1.0, 2.0, 5.0, 7.0, 10.0, 20.0, 30.0)

#: Furthest a trade may sit from a bucket, as a fraction of that bucket.
MAX_BUCKET_DRIFT = 0.25

#: Below this a node is marked thin rather than dropped: one print is still
#: the only thing the market said about that point.
MIN_OBSERVATIONS_FOR_CONFIDENT = 3


@dataclass(frozen=True)
class VolNode:
    """One grid point and everything behind it."""

    expiry_years: float
    tenor_years: float
    volatility: float
    observations: int
    #: Spread of the prints behind this node, as a fraction of the median. A
    #: node whose trades disagreed by 40% is not the same as one whose agreed
    #: to 2%, and the number alone cannot say so.
    dispersion: float

    @property
    def is_confident(self) -> bool:
        return self.observations >= MIN_OBSERVATIONS_FOR_CONFIDENT


@dataclass(frozen=True)
class VolSurface:
    """A grid of observed nodes, and what was left out of it."""

    as_of: date
    currency: str
    nodes: tuple[VolNode, ...]
    #: Trades excluded for sitting outside the moneyness band, and for
    #: matching no grid point. Reported rather than dropped silently: a
    #: surface covering only the middle of the market must say so.
    excluded_off_the_money: int
    excluded_no_bucket: int
    #: How many trading days the prints came from. One is a surface; more is
    #: an average of that many surfaces, and the two must not be read alike.
    pooled_days: int = 1

    def at(self, expiry_years: float, tenor_years: float) -> VolNode | None:
        """The node at a grid point, or `None`.

        Never the nearest neighbour. An interpolated volatility looks exactly
        like an observed one on a screen, and this surface's whole claim is
        that its numbers were paid.
        """
        for node in self.nodes:
            if node.expiry_years == expiry_years and node.tenor_years == tenor_years:
                return node
        return None

    @property
    def coverage(self) -> float:
        """Fraction of the standard grid that has any observation at all."""
        return len(self.nodes) / (len(EXPIRY_BUCKETS) * len(TENOR_BUCKETS))


class EmptySurfaceError(ValueError):
    """No trade survived the filters, with the counts that led there.

    Raised rather than returning an empty grid: "no swaptions traded" and "a
    surface with no points" render the same and mean different things.
    """


def _bucket(value: float, buckets: tuple[float, ...]) -> float | None:
    nearest = min(buckets, key=lambda b: abs(b - value))
    return nearest if abs(nearest - value) <= nearest * MAX_BUCKET_DRIFT else None


@model(
    model_id="vol.swaption_surface",
    version="1.0",
    spec_section="§11.3",
    summary="Swaption volatility grid from transacted prints, observed nodes only",
)
def build_surface(
    quotes: Sequence[tuple[SwaptionQuote, float, float]],
    *,
    as_of: date,
    currency: str,
    moneyness_band: float = DEFAULT_MONEYNESS_BAND,
    pool_days: bool = False,
) -> VolSurface:
    """Aggregate `(quote, forward, implied volatility)` triples into a grid.

    The forward comes in alongside because moneyness needs it and this module
    does not build curves — keeping the curve dependency out is what lets the
    surface be tested without one.

    **One trading day, unless told otherwise.** Prints from other days are
    refused rather than averaged in. Volatility moves, so pooling a fortnight
    at one node reports the average of a fortnight's surfaces as though it
    were today's — the same failure as a curve whose front end is March's and
    whose long end is May's, which `SWPM` refuses for the same reason.

    Measured on fifteen days of EUR prints: pooling raises grid coverage from
    21% to 79% and median node dispersion from under 20% to 88%, with
    individual nodes at 376%. The coverage is real and the agreement is not.
    `pool_days=True` allows it deliberately, and the result records how many
    days it spans so nothing downstream can mistake it for one.
    """
    if moneyness_band <= 0.0:
        raise ValueError("a moneyness band of zero admits nothing; the surface would be empty")

    days = {quote.traded for quote, _forward, _vol in quotes}
    if not pool_days and days - {as_of}:
        raise ValueError(
            f"prints from {len(days)} trading days were given for a surface as of {as_of}. "
            "Volatility moves, so pooling them averages that many surfaces into one; pass "
            "pool_days=True to do it on purpose"
        )

    off_money = no_bucket = 0
    collected: dict[tuple[float, float], list[float]] = {}
    for quote, forward, volatility in quotes:
        if forward <= 0.0 or abs(quote.strike / forward - 1.0) > moneyness_band:
            off_money += 1
            continue
        expiry = _bucket(quote.expiry_years, EXPIRY_BUCKETS)
        tenor = _bucket(quote.tenor_years, TENOR_BUCKETS)
        if expiry is None or tenor is None:
            no_bucket += 1
            continue
        collected.setdefault((expiry, tenor), []).append(volatility)

    if not collected:
        raise EmptySurfaceError(
            f"no trade survived: {off_money} outside the {moneyness_band:.0%} moneyness band "
            f"and {no_bucket} matching no grid point. An empty surface and an untraded "
            "market render the same and are not the same"
        )

    nodes = []
    for (expiry, tenor), values in sorted(collected.items()):
        median = statistics.median(values)
        spread = (max(values) - min(values)) / median if median else 0.0
        nodes.append(
            VolNode(
                expiry_years=expiry,
                tenor_years=tenor,
                volatility=median,
                observations=len(values),
                dispersion=spread,
            )
        )
    return VolSurface(
        as_of=as_of,
        currency=currency,
        nodes=tuple(nodes),
        excluded_off_the_money=off_money,
        excluded_no_bucket=no_bucket,
        pooled_days=len(days),
    )


__all__ = [
    "DEFAULT_MONEYNESS_BAND",
    "EXPIRY_BUCKETS",
    "MAX_BUCKET_DRIFT",
    "MIN_OBSERVATIONS_FOR_CONFIDENT",
    "TENOR_BUCKETS",
    "EmptySurfaceError",
    "VolNode",
    "VolSurface",
    "build_surface",
]
