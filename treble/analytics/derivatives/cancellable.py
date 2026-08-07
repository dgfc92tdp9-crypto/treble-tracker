"""Cancellable and extendible swaps (spec §12.1).

**Both are a vanilla swap plus or minus one swaption, and that is exact.**
The right to cancel a payer swap at date T is the right to enter the
offsetting receiver swap at T on whatever remains — so a cancellable payer
swap is a vanilla payer swap to final maturity *minus* a receiver swaption
expiring at T. The right to extend is the right to enter the extension
itself, so an extendible payer swap is a vanilla payer swap to the short
maturity *plus* a payer swaption at T.

This is the same move `capfloor.py` makes, for the same reason: the
decomposition is a restatement of the contract rather than an approximation,
so the option is priced by the swaption pricers that are already tested
instead of by a second implementation that would drift from them.

**The cancellation right belongs to one side, and which side changes the
sign.** A swap the *payer* may cancel is worth less to the payer than the
vanilla — they are short nothing, they are long an option, so the vanilla
must give up value to pay for it. Getting this backwards produces a
cancellable that is worth more than the vanilla it is built from, which is
the one error in this module that would look plausible on a screen.

**What is not modelled, said plainly.** This prices a *single* exercise
date. A Bermudan cancellable, which is what most callable swaps actually
are, needs a lattice or a least-squares Monte Carlo across the whole
exercise schedule, and the difference is real: the right to choose among
many dates is worth strictly more than the right to choose on one.
:func:`cancellable_swap` refuses a schedule rather than pricing the first
date and calling it the answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from treble.analytics.registry import model
from treble.analytics.vol.swaption import bachelier_swaption, black_swaption

_BLACK = black_swaption.__wrapped__  # type: ignore[attr-defined]
_BACHELIER = bachelier_swaption.__wrapped__  # type: ignore[attr-defined]


@dataclass(frozen=True)
class CancellablePricing:
    """A cancellable or extendible swap, decomposed.

    Every part is carried rather than only the total: the whole claim of
    this module is that the structure *is* a swap plus an option, and a
    single number cannot show whether the option was worth anything.
    """

    value: float
    #: PV of the underlying vanilla swap, to the structure's own maturity.
    vanilla_value: float
    #: PV of the embedded swaption. Never negative — it is an option.
    option_value: float
    #: True when the option was priced with a normal (Bachelier) vol.
    normal_vol: bool

    @property
    def option_share(self) -> float:
        """Fraction of the absolute value that the option accounts for."""
        total = abs(self.vanilla_value) + self.option_value
        return self.option_value / total if total > 0.0 else 0.0


def _swaption(
    *,
    forward: float,
    strike: float,
    expiry_years: float,
    volatility: float,
    annuity: float,
    payer: bool,
    normal_vol: bool,
) -> float:
    if expiry_years <= 0.0:
        raise ValueError(
            "the cancellation right expires at or before today, so there is nothing "
            "left to decide; this is a vanilla swap, not a cancellable one"
        )
    if volatility < 0.0:
        raise ValueError("a negative volatility is not a cheap option, it is a bad input")
    pricer = _BACHELIER if normal_vol else _BLACK
    value: float = pricer(
        forward=forward,
        strike=strike,
        expiry_years=expiry_years,
        volatility=volatility,
        annuity=annuity,
        payer=payer,
    )
    return value


@model(
    model_id="derivatives.cancellable_swap",
    version="1.0",
    spec_section="§12.1",
    summary="Cancellable swap as a vanilla swap less the offsetting swaption",
)
def cancellable_swap(
    *,
    vanilla_value: float,
    forward: float,
    strike: float,
    expiry_years: float,
    volatility: float,
    annuity: float,
    payer: bool = True,
    normal_vol: bool = True,
) -> CancellablePricing:
    """A swap the holder may cancel once, at `expiry_years`.

    `vanilla_value` is the PV of the swap run to final maturity, from the
    perspective of the party holding the cancellation right. `forward`,
    `annuity` and `expiry_years` describe the *remaining* swap at the
    cancellation date — that is what the offsetting swaption is on, and
    passing the whole swap's annuity here is the mistake that makes a
    cancellable look far too valuable.
    """
    # The right to cancel a payer is the right to receive, and vice versa.
    option = _swaption(
        forward=forward,
        strike=strike,
        expiry_years=expiry_years,
        volatility=volatility,
        annuity=annuity,
        payer=not payer,
        normal_vol=normal_vol,
    )
    return CancellablePricing(
        # Minus, not plus. The holder pays for the right, so the package is
        # worth less to them than the vanilla. A cancellable worth more than
        # the swap it is built from is the plausible-looking error here.
        value=vanilla_value - option,
        vanilla_value=vanilla_value,
        option_value=option,
        normal_vol=normal_vol,
    )


@model(
    model_id="derivatives.extendible_swap",
    version="1.0",
    spec_section="§12.1",
    summary="Extendible swap as a short vanilla swap plus the extension swaption",
)
def extendible_swap(
    *,
    vanilla_value: float,
    forward: float,
    strike: float,
    expiry_years: float,
    volatility: float,
    annuity: float,
    payer: bool = True,
    normal_vol: bool = True,
) -> CancellablePricing:
    """A swap the holder may extend once, at `expiry_years`.

    `vanilla_value` is the PV of the swap to its *short* maturity; the
    swaption is on the extension period. The holder is long the option here
    too, but the option is added rather than subtracted because the
    underlying stops short — they are buying something extra, not giving
    something up.
    """
    option = _swaption(
        forward=forward,
        strike=strike,
        expiry_years=expiry_years,
        volatility=volatility,
        annuity=annuity,
        payer=payer,
        normal_vol=normal_vol,
    )
    return CancellablePricing(
        value=vanilla_value + option,
        vanilla_value=vanilla_value,
        option_value=option,
        normal_vol=normal_vol,
    )


__all__ = ["CancellablePricing", "cancellable_swap", "extendible_swap"]
