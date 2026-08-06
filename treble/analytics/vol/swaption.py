"""Swaption implied volatility from transacted premiums (spec §11.3, §12.1).

§12.1 lists swaptions, caps/floors, CMS and cancellable swaps, and all four
need a volatility. That was recorded here as needing a vol surface no free
source provides. It turns out one does: the CFTC Part 43 tape this project
already ingests for curve building carries **option premiums, strikes,
exercise dates and underlier maturities** on real swaption trades — 406 with
a full expiry-into-tenor in a single day's file, across USD SOFR, EUR, GBP
and JPY.

So the vol is not quoted, but it is *implied* by prints the market actually
paid, which is the same relationship this project already relies on for its
curves: transacted rather than indicative.

**Black, not Bachelier, and the choice is stated.** Rates here are positive
across the tenors on the tape, so lognormal is workable and is what the
market quotes swaption vol in for USD and GBP. EUR at negative or near-zero
rates needs normal vol, and Black's formula has no answer there rather than
a wrong one — :func:`implied_black_vol` refuses instead of returning a
number, because a lognormal vol on a negative forward is not a large number,
it is undefined.

**A premium is not a mid.** These are executed trades: one counterparty's
price at one moment, possibly a block trade whose notional the CFTC caps.
An implied vol from one print is a data point, not a surface, and nothing
here interpolates between them.

**Run against the live tape, this does not yet produce a usable surface.**
61 EUR swaptions solved on the 2026-07-13 file, and the results are too
dispersed to interpolate: two receivers with the same strike and forward
implied 68.0% and 7.7%, and deep out-of-the-money receivers implied 137-156%.
**The dispersion was substantially my own doing, found on the third
attempt.** The exploratory script that produced those numbers priced
2026-07-13 prints against a 2026-07-31 curve — an eighteen-day-stale forward,
which puts the moneyness of every trade in the wrong place and inflates the
wings most. Running the same code with the print day matched to the curve
day: node dispersion falls from 84-105% to 0-17%, the share of trades outside
the moneyness band from 16% to 4%, and p90/p10 across all solved vols from
9.53 to 2.64.

It is a contributor rather than the whole story — the lag effect across
fifteen days is noisy, and 2.64x dispersion at zero lag is still wide for a
market surface. But two earlier commits recorded the wings as unexplained
when the leading explanation was a date mismatch in the measurement, which
is the same failure as reporting an untested attribution: the difference is
only that this one was in a script rather than a docstring.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from scipy.optimize import brentq

from treble.analytics.registry import model

#: The widest volatility a solve may return. A premium that implies more than
#: this is a data problem — a capped notional, a premium in the wrong
#: currency, a mis-parsed strike — rather than a 500%-vol swaption, and
#: returning it would put that number on a surface.
MAX_VOL = 3.0

#: The narrowest. Below this the option is worth its intrinsic value and the
#: solve is fitting noise in the last decimal of the premium.
MIN_VOL = 1e-4


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass(frozen=True)
class SwaptionQuote:
    """One transacted swaption, as the tape reports it."""

    #: True for a payer (right to pay fixed), False for a receiver.
    payer: bool
    expiry: date
    #: Maturity of the underlying swap, not of the option.
    underlier_maturity: date
    strike: float
    #: Premium as a fraction of notional. The tape gives both in the same
    #: currency; dividing here keeps the vol solve scale-free.
    premium_fraction: float
    currency: str
    traded: date
    #: Whether the CFTC capped the notional on this print. A capped notional
    #: makes `premium_fraction` too large — the premium is the real one and
    #: the notional is a floor — so the implied vol is biased upward and the
    #: quote says so rather than being silently averaged in.
    notional_capped: bool = False

    @property
    def expiry_years(self) -> float:
        return (self.expiry - self.traded).days / 365.25

    @property
    def tenor_years(self) -> float:
        return (self.underlier_maturity - self.expiry).days / 365.25


@model(
    model_id="vol.black_swaption",
    version="1.0",
    spec_section="§11.3",
    summary="Black lognormal swaption price per unit notional",
)
def black_swaption(
    *,
    forward: float,
    strike: float,
    expiry_years: float,
    volatility: float,
    annuity: float,
    payer: bool = True,
) -> float:
    """Black's formula for a European swaption, per unit notional.

    `annuity` carries the discounting and the underlying's accrual; the
    forward and strike carry the rate. Keeping them separate is what lets
    this be used against the multi-curve environment rather than a flat rate.
    """
    if expiry_years <= 0.0:
        raise ValueError("an expired option has no time value; its price is intrinsic")
    if forward <= 0.0 or strike <= 0.0:
        raise ValueError(
            f"Black is lognormal and has no answer for a forward of {forward} against a "
            f"strike of {strike}. A negative-rate market needs a normal (Bachelier) vol, "
            "and returning a number here would be inventing one"
        )
    if volatility <= 0.0:
        intrinsic = max(forward - strike, 0.0) if payer else max(strike - forward, 0.0)
        return annuity * intrinsic

    total = volatility * math.sqrt(expiry_years)
    d1 = (math.log(forward / strike) + 0.5 * total**2) / total
    d2 = d1 - total
    if payer:
        return annuity * (forward * _norm_cdf(d1) - strike * _norm_cdf(d2))
    return annuity * (strike * _norm_cdf(-d2) - forward * _norm_cdf(-d1))


class ImpliedVolError(ValueError):
    """No volatility reproduces this premium, with the reason.

    Raised rather than returning a bound: a premium below intrinsic or above
    the forward is a data problem, and clamping it to MIN_VOL or MAX_VOL puts
    a fabricated number on a surface where it is indistinguishable from a
    solved one.
    """


@model(
    model_id="vol.implied_black_vol",
    version="1.0",
    spec_section="§11.3",
    summary="Lognormal volatility implied by a transacted swaption premium",
)
def implied_black_vol(
    *,
    premium_fraction: float,
    forward: float,
    strike: float,
    expiry_years: float,
    annuity: float,
    payer: bool = True,
) -> float:
    """Solve Black for the volatility that reproduces a traded premium."""
    if premium_fraction <= 0.0:
        raise ImpliedVolError("a premium of zero or less implies no volatility")
    if forward <= 0.0 or strike <= 0.0:
        raise ImpliedVolError(
            f"forward {forward:.6f} or strike {strike:.6f} is not positive, so a lognormal "
            "vol does not exist. This is a Bachelier market, not a high-vol one"
        )

    intrinsic = annuity * (max(forward - strike, 0.0) if payer else max(strike - forward, 0.0))
    if premium_fraction < intrinsic - 1e-12:
        raise ImpliedVolError(
            f"premium {premium_fraction:.8f} is below intrinsic {intrinsic:.8f}; no "
            "volatility is low enough, so the inputs disagree rather than the option "
            "being cheap"
        )

    def gap(vol: float) -> float:
        priced: float = black_swaption.__wrapped__(  # type: ignore[attr-defined]
            forward=forward,
            strike=strike,
            expiry_years=expiry_years,
            volatility=vol,
            annuity=annuity,
            payer=payer,
        )
        return priced - premium_fraction

    if gap(MAX_VOL) < 0.0:
        raise ImpliedVolError(
            f"premium {premium_fraction:.8f} exceeds the Black price at {MAX_VOL:.0%} vol. "
            "A capped notional, a premium in another currency or a mis-parsed strike "
            "explains this; a swaption does not"
        )
    if gap(MIN_VOL) > 0.0:
        raise ImpliedVolError(
            f"premium {premium_fraction:.8f} is below the Black price at {MIN_VOL:.2%} vol"
        )
    return float(brentq(gap, MIN_VOL, MAX_VOL, xtol=1e-10))


@model(
    model_id="vol.bachelier_swaption",
    version="1.0",
    spec_section="§11.3",
    summary="Bachelier (normal) swaption price per unit notional",
)
def bachelier_swaption(
    *,
    forward: float,
    strike: float,
    expiry_years: float,
    volatility: float,
    annuity: float,
    payer: bool = True,
) -> float:
    """Normal-vol swaption price, per unit notional.

    The convention EUR and JPY swaptions are quoted in, and the one that
    works when a forward or strike is at or below zero — where Black has no
    answer at all rather than a large one.
    """
    if expiry_years <= 0.0:
        raise ValueError("an expired option has no time value; its price is intrinsic")
    sign = 1.0 if payer else -1.0
    moneyness = sign * (forward - strike)
    if volatility <= 0.0:
        return annuity * max(moneyness, 0.0)
    total = volatility * math.sqrt(expiry_years)
    d = moneyness / total
    density = math.exp(-0.5 * d * d) / math.sqrt(2.0 * math.pi)
    return annuity * (moneyness * _norm_cdf(d) + total * density)


@model(
    model_id="vol.implied_normal_vol",
    version="1.0",
    spec_section="§11.3",
    summary="Normal volatility implied by a transacted swaption premium",
)
def implied_normal_vol(
    *,
    premium_fraction: float,
    forward: float,
    strike: float,
    expiry_years: float,
    annuity: float,
    payer: bool = True,
) -> float:
    """Solve Bachelier for the volatility that reproduces a traded premium.

    **Why this exists.** EUR and JPY swaptions are quoted in normal vol, and
    Bachelier is defined where Black is not — at or below a zero forward. It
    belongs here for both reasons independently of anything below.

    **What it does not explain.** It was added to test a hypothesis about the
    live tape: Black gave a median 39.1% within 10% of the money and 137.5%
    outside, which looks like the signature of reading a normal-vol market
    lognormally. Measured, the hypothesis fails. Normal vol shows the same
    shape — 120.6bp within 10% of the money against 329.3bp outside, a 2.7x
    step where Black's was 3.5x — and comparable dispersion (p90/p10 of 5.30
    against Black's 5.03). Both measures price those nine wing trades at
    roughly three times the at-the-money level, which is too steep for a
    smile in either convention.

    So the convention is not the answer, and the wing anomaly stands with two
    explanations now eliminated rather than one. What remains untested: the
    premium field carrying more than the option premium, the notional not
    being the swaption's, or those rows not being plain European swaptions
    despite the FISN.
    """
    if premium_fraction <= 0.0:
        raise ImpliedVolError("a premium of zero or less implies no volatility")
    intrinsic = annuity * max((forward - strike) if payer else (strike - forward), 0.0)
    if premium_fraction < intrinsic - 1e-12:
        raise ImpliedVolError(
            f"premium {premium_fraction:.8f} is below intrinsic {intrinsic:.8f}; no "
            "volatility is low enough, so the inputs disagree"
        )

    def gap(vol: float) -> float:
        priced: float = bachelier_swaption.__wrapped__(  # type: ignore[attr-defined]
            forward=forward,
            strike=strike,
            expiry_years=expiry_years,
            volatility=vol,
            annuity=annuity,
            payer=payer,
        )
        return priced - premium_fraction

    # Normal vol is an absolute rate move, so its scale is basis points
    # rather than percent: 1e-6 is 0.0001bp and 0.10 is 1,000bp a year.
    low, high = 1e-6, 0.10
    if gap(high) < 0.0:
        raise ImpliedVolError(
            f"premium {premium_fraction:.8f} exceeds the Bachelier price at "
            f"{high * 1e4:.0f}bp normal vol; the inputs disagree rather than the market "
            "being that volatile"
        )
    if gap(low) > 0.0:
        raise ImpliedVolError(f"premium {premium_fraction:.8f} is below the price at 0.01bp vol")
    return float(brentq(gap, low, high, xtol=1e-12))


__all__ = [
    "MAX_VOL",
    "MIN_VOL",
    "ImpliedVolError",
    "SwaptionQuote",
    "bachelier_swaption",
    "black_swaption",
    "implied_black_vol",
    "implied_normal_vol",
]
