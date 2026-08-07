"""Constant maturity swaps and the convexity adjustment (spec §12.1).

A CMS leg pays a *swap* rate — the 10-year rate, say — on a quarterly
schedule, rather than the 3-month rate a vanilla leg pays. The awkwardness is
that a swap rate is naturally a martingale under the measure associated with
its own annuity, and a CMS payment is not made on that annuity. The
difference is the **convexity adjustment**, and it is not a refinement: at
long tenors and realistic volatility it is tens of basis points, and a CMS
leg priced at the plain forward swap rate is wrong in one direction, always
too low.

**This is Hull's approximation, and calling it that matters.**

    E[S_T] ≈ S₀ - ½ · S₀² · σ² · T · G''(S₀) / G'(S₀)

where `G(y)` is the annuity of the underlying swap as a function of its own
rate. It assumes a lognormal swap rate and a single volatility, and it is a
second-order expansion of the measure change rather than an identity.

**The accurate method is replication**, which recovers the adjustment by
integrating the payoff against a strip of swaptions across strikes, and so
uses the whole smile rather than one number. That is strictly better, and
this repository has the pieces for it — a swaption pricer and a fitted
surface — but the surface's own dispersion is wide enough (median node
dispersion 55% at the chosen half-life) that integrating across it would
carry that noise into every CMS number while looking more rigorous.
Approximating from one vol and saying so is the more honest of the two
until the surface is tighter, and this docstring is where that trade is
recorded rather than discovered later.

**The adjustment is positive, always.** `G` is decreasing and convex in the
rate, so `-G''/G'` is positive, and higher volatility makes a CMS leg worth
more rather than less. A sign error here produces a CMS rate below the
forward swap rate, which is the one result that should never appear.
"""

from __future__ import annotations

from dataclasses import dataclass

from treble.analytics.registry import model


@dataclass(frozen=True)
class CmsRate:
    """A convexity-adjusted CMS rate, with the adjustment shown separately."""

    #: The adjusted rate: what the CMS leg is expected to pay.
    rate: float
    #: The plain forward swap rate it started from.
    forward_swap_rate: float
    #: The adjustment, in basis points. Carried apart because it is the
    #: whole content of this module — a screen showing only the rate cannot
    #: say whether the adjustment was 2bp or 40bp, and the second is a
    #: number somebody should look at before trading on it.
    adjustment_bp: float


def _annuity_derivatives(rate: float, payments: int, frequency: int) -> tuple[float, float]:
    """`G'(y)` and `G''(y)` for a level annuity at rate `y`.

    Computed analytically rather than by bumping. A finite difference here
    would need a step small enough for the second derivative to be accurate
    and large enough not to be dominated by floating point, and the whole
    adjustment is a ratio of the two — so the noise would land directly on
    the number this module exists to produce.
    """
    per_period = rate / frequency
    base = 1.0 + per_period
    first = 0.0
    second = 0.0
    for i in range(1, payments + 1):
        first -= i / frequency * base ** (-i - 1)
        second += i * (i + 1) / (frequency**2) * base ** (-i - 2)
    return first, second


@model(
    model_id="derivatives.cms_rate",
    version="1.0",
    spec_section="§12.1",
    summary="Convexity-adjusted CMS rate (Hull's approximation)",
)
def cms_rate(
    *,
    forward_swap_rate: float,
    volatility: float,
    expiry_years: float,
    tenor_years: float,
    frequency: int = 2,
) -> CmsRate:
    """The expected CMS payment, adjusted for convexity.

    `volatility` is the *lognormal* volatility of the underlying swap rate,
    because Hull's expansion is derived under a lognormal rate. A normal vol
    passed here would be off by roughly a factor of the forward and would
    make the adjustment look tiny rather than wrong.
    """
    if forward_swap_rate <= 0.0:
        raise ValueError(
            "Hull's adjustment is derived under a lognormal swap rate and has no answer "
            "at or below zero. A negative-rate CMS needs a normal-vol treatment, and "
            "returning a number here would be inventing one"
        )
    if volatility < 0.0:
        raise ValueError("a negative volatility is not a small adjustment, it is a bad input")
    if expiry_years < 0.0:
        raise ValueError("a negative time to expiry runs the adjustment backwards")
    if tenor_years <= 0.0 or frequency <= 0:
        raise ValueError("the underlying swap needs a positive tenor and payment frequency")

    payments = max(round(tenor_years * frequency), 1)
    first, second = _annuity_derivatives(forward_swap_rate, payments, frequency)
    # G is decreasing and convex, so -G''/G' is positive and the adjustment
    # raises the rate. A CMS rate below the forward swap rate is the one
    # result that should never appear.
    adjustment = 0.5 * forward_swap_rate**2 * volatility**2 * expiry_years * (-second / first)
    return CmsRate(
        rate=forward_swap_rate + adjustment,
        forward_swap_rate=forward_swap_rate,
        adjustment_bp=adjustment * 1e4,
    )


__all__ = ["CmsRate", "cms_rate"]
