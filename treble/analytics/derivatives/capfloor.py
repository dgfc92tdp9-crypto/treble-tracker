"""Caps and floors (spec §12.1).

**A caplet is a one-period payer swaption.** Both are an option to pay a
fixed rate against a floating one; the caplet's underlying swap has a single
period, so its annuity is that period's accrual times its discount factor.
That is not an approximation — it is the same contract written twice — so
this module calls :func:`~treble.analytics.vol.swaption.black_swaption` and
:func:`~treble.analytics.vol.swaption.bachelier_swaption` rather than
restating their formulas.

Restating them is the tempting version and the wrong one. Two copies of
Black's formula drift: one gets a fix for a zero-vol edge case, the other
does not, and the discrepancy shows up as a cap and a swaption disagreeing
about the same option. The vol module already refuses a lognormal solve on a
negative forward, already returns intrinsic at zero vol, and is already
tested for both; a second implementation would need all of that again to be
worth the same trust.

**Both conventions, because the market uses both.** EUR and JPY caps are
quoted in normal vol and can trade through zero, where Black has no answer
rather than a large one. The caller states which, and the result says which
it was priced under — a 90 that is basis points and a 90 that is percent
render identically.

**Parity is a real check, and it is narrower than it looks.** `cap - floor`
must equal the value of paying the strike on the same strip, for any vol,
both conventions, every strike, and it catches a dropped period or an option
priced on the wrong side.

It does **not** catch a wrong annuity, and the first version of this
docstring claimed it did. The annuity multiplies the caplet values and the
strike leg alike, so it cancels: hard-coding `accrual` to a constant leaves
every parity assertion passing, including the deliberately irregular
schedule written to catch exactly that. Verified by doing it — 27 of 27
still green.

So the annuity is pinned directly instead (`accrual * discount_factor`, as a
property test), and the absolute level is anchored against the closed-form
ATM Bachelier price `annuity * vol * sqrt(T) / sqrt(2*pi)`, computed without
going through the pricer. Those are the checks that fail when the annuity is
wrong. Parity earns its place beside them, not instead of them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from treble.analytics.registry import model
from treble.analytics.vol.swaption import bachelier_swaption, black_swaption

#: Price a caplet through the swaption pricers rather than duplicating them.
_BLACK = black_swaption.__wrapped__  # type: ignore[attr-defined]
_BACHELIER = bachelier_swaption.__wrapped__  # type: ignore[attr-defined]


@dataclass(frozen=True)
class Caplet:
    """One period of a cap or floor, with its market data already resolved.

    The forward and the discount factor come in rather than a curve, for the
    reason `vol/surface.py` takes them that way: keeping the curve dependency
    out is what lets this be tested without building one, and what lets the
    same code price off a multi-curve environment where the forecast and
    discount curves differ.
    """

    #: Forward rate for the period, from the forecast curve.
    forward: float
    #: Year fraction of the accrual period, on the leg's own day count.
    accrual: float
    #: Discount factor to the period's payment date, from the discount curve.
    discount_factor: float
    #: Time to the caplet's fixing, in years. The option expires at the start
    #: of the period it pays on, not at the payment date.
    expiry_years: float

    @property
    def annuity(self) -> float:
        """The one-period annuity that makes this a swaption."""
        return self.accrual * self.discount_factor


@dataclass(frozen=True)
class CapPricing:
    """A priced cap or floor, and the convention it was priced under."""

    value: float
    #: True if the quote was a normal (Bachelier) vol, False for lognormal.
    normal_vol: bool
    #: Per-caplet values, in the order given. A cap is a strip and the strip
    #: is where a wrong period hides; a single number cannot show that one
    #: caplet is carrying the whole price.
    caplets: tuple[float, ...]
    #: Value of paying `strike` on the same schedule — the underlying of the
    #: parity relationship, carried so a caller can check it rather than
    #: trust it.
    strike_annuity_value: float


def _one(
    caplet: Caplet, *, strike: float, volatility: float, normal_vol: bool, payer: bool
) -> float:
    pricer = _BACHELIER if normal_vol else _BLACK
    if caplet.expiry_years <= 0.0:
        # Already fixed: no time value left, only what it settles for.
        intrinsic = (
            max(caplet.forward - strike, 0.0) if payer else max(strike - caplet.forward, 0.0)
        )
        return caplet.annuity * intrinsic
    value: float = pricer(
        forward=caplet.forward,
        strike=strike,
        expiry_years=caplet.expiry_years,
        volatility=volatility,
        annuity=caplet.annuity,
        payer=payer,
    )
    return value


def _price(
    caplets: Sequence[Caplet],
    *,
    strike: float,
    volatility: float,
    normal_vol: bool,
    payer: bool,
) -> CapPricing:
    if not caplets:
        raise ValueError(
            "a cap with no periods has no value and no meaning; an empty schedule is a "
            "construction error rather than a zero-value trade"
        )
    if volatility < 0.0:
        raise ValueError("a negative volatility is not a cheap option, it is a bad input")
    values = tuple(
        _one(caplet, strike=strike, volatility=volatility, normal_vol=normal_vol, payer=payer)
        for caplet in caplets
    )
    return CapPricing(
        value=sum(values),
        normal_vol=normal_vol,
        caplets=values,
        strike_annuity_value=sum(c.annuity * (c.forward - strike) for c in caplets),
    )


@model(
    model_id="derivatives.cap",
    version="1.0",
    spec_section="§12.1",
    summary="Cap as a strip of caplets, priced as one-period payer swaptions",
)
def price_cap(
    caplets: Sequence[Caplet],
    *,
    strike: float,
    volatility: float,
    normal_vol: bool = True,
) -> CapPricing:
    """Value a cap: the right to receive `forward - strike` when positive."""
    return _price(caplets, strike=strike, volatility=volatility, normal_vol=normal_vol, payer=True)


@model(
    model_id="derivatives.floor",
    version="1.0",
    spec_section="§12.1",
    summary="Floor as a strip of floorlets, priced as one-period receiver swaptions",
)
def price_floor(
    caplets: Sequence[Caplet],
    *,
    strike: float,
    volatility: float,
    normal_vol: bool = True,
) -> CapPricing:
    """Value a floor: the right to receive `strike - forward` when positive."""
    return _price(caplets, strike=strike, volatility=volatility, normal_vol=normal_vol, payer=False)


__all__ = ["CapPricing", "Caplet", "price_cap", "price_floor"]
