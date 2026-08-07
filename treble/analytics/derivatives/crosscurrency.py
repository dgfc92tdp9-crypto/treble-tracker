"""Cross-currency swaps (spec §12.1).

Two floating legs in different currencies, with the notionals **exchanged**
at the start and returned at maturity. That exchange is the whole difference
from a single-currency basis swap, and it is where the risk lives: the
value moves with FX directly, not merely through the coupons.

**Priced from inputs, not from a live basis curve.** An earlier commit
recorded cross-currency as blocked because this repository has no source for
FX forwards. That was true of *building the curve* and false of the pricer,
which is the same shape as every other §12.1 module here — `capfloor` takes
forwards, `cms` takes a vol, this takes discount factors and a spot rate.
What remains genuinely unsourced is the cross-currency basis itself, and
that is recorded on the criterion rather than hidden behind a default.

**The basis spread sits on one leg by convention, and which one is not a
detail.** The market quotes the basis against USD, on the non-USD leg. A
spread applied to the wrong leg has the right magnitude and the wrong sign
in the P&L, which is the error most likely to survive a review because every
number in the output still looks reasonable.

**Discount each leg on its own currency's curve.** A CSA in one currency
does not discount the other's cashflows, and using one curve for both is a
mistake that grows with the basis — precisely when the trade is interesting.

**Resettable notionals are not modelled here.** A mark-to-market
cross-currency swap resets the foreign notional to spot at each period,
which removes most of the FX exposure between resets and changes the
instrument materially. This prices the fixed-notional form. Passing a
resettable trade through it would overstate the FX sensitivity, so
:class:`CrossCurrencySpec` records which form it is rather than leaving the
reader to assume.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from treble.analytics.registry import model


class CrossCurrencySpec(BaseModel):
    """A fixed-notional cross-currency swap, from the domestic side."""

    model_config = ConfigDict(frozen=True)

    #: Notional of the domestic leg, in domestic currency.
    domestic_notional: float = Field(gt=0.0)
    #: Notional of the foreign leg, in foreign currency. Set at inception
    #: from the spot rate and fixed thereafter.
    foreign_notional: float = Field(gt=0.0)
    #: Cross-currency basis, in decimal, applied to the foreign leg by
    #: market convention. Negative is the common case for most currencies
    #: against USD.
    basis_spread: float
    #: True when the trade resets its foreign notional to spot each period.
    #: This module prices the fixed-notional form and refuses the other,
    #: rather than pricing it as if the reset were not there.
    resettable: bool = False


@dataclass(frozen=True)
class CrossCurrencyPricing:
    """A cross-currency swap value, with both legs in domestic terms."""

    value: float
    #: PV of the domestic leg including its notional exchange.
    domestic_leg: float
    #: PV of the foreign leg, converted at spot.
    foreign_leg: float
    #: What the basis spread alone contributes, in domestic currency.
    basis_value: float


@model(
    model_id="derivatives.cross_currency_swap",
    version="1.0",
    spec_section="§12.1",
    summary="Fixed-notional cross-currency swap valued in domestic currency",
)
def price_cross_currency_swap(
    spec: CrossCurrencySpec,
    *,
    spot: float,
    domestic_float_pv: float,
    foreign_float_pv: float,
    domestic_final_df: float,
    foreign_final_df: float,
    foreign_annuity: float,
    receive_domestic: bool = True,
) -> CrossCurrencyPricing:
    """Value a cross-currency swap in domestic currency.

    `spot` is domestic units per foreign unit. The two `*_float_pv` figures
    are each leg's floating coupons per unit of that leg's own notional,
    discounted on that leg's own curve; `*_final_df` discount the notional
    returned at maturity; `foreign_annuity` is the foreign leg's annuity,
    which the basis spread accrues on.
    """
    if spot <= 0.0:
        raise ValueError("a spot rate of zero or less is a data error, not a free currency")
    if spec.resettable:
        raise ValueError(
            "this prices the fixed-notional form. A mark-to-market cross-currency swap "
            "resets its foreign notional to spot each period, which removes most of the "
            "FX exposure between resets; pricing it here would overstate that exposure "
            "rather than approximate it"
        )

    # Each leg carries its coupons and the notional returned at maturity.
    # The initial exchange nets to zero at inception by construction, so it
    # is the *final* exchange that shows up in the valuation.
    domestic = spec.domestic_notional * (domestic_float_pv + domestic_final_df)
    basis = spec.foreign_notional * spec.basis_spread * foreign_annuity
    foreign_local = spec.foreign_notional * (foreign_float_pv + foreign_final_df) + basis
    foreign = foreign_local * spot

    net = domestic - foreign
    return CrossCurrencyPricing(
        value=net if receive_domestic else -net,
        domestic_leg=domestic,
        foreign_leg=foreign,
        basis_value=basis * spot,
    )


__all__ = ["CrossCurrencyPricing", "CrossCurrencySpec", "price_cross_currency_swap"]
