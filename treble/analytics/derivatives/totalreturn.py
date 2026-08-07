"""Total return swaps (spec §12.1).

One leg pays the total return of an asset — price change plus any income —
and the other pays a funding rate on the same notional. Between reset dates
the value is the difference in what each leg has accrued, discounted to
today.

**Income is a separate term, and it is not optional.** A total return leg
pays dividends and coupons as well as price appreciation, so a valuation
that used only the price change would understate it by exactly the income —
systematically, and most for the high-yielding assets people put in these
structures precisely to get the income. :func:`total_return_swap` takes it
explicitly rather than defaulting it to zero, so an asset that paid
something cannot silently be valued as if it had not.

That is a different question from the one the equity price adapter answers.
`ADJ_CLOSE` is already a total return, so a TRS marked off *that* series
must pass `income=0.0` or count the dividends twice. The parameter is
required rather than defaulted for that reason: the right value depends on
which series the caller marked against, and a default would be wrong half
the time without saying so.

**The reset price is the contract's, not the market's most recent.** A TRS
resets on a schedule, and the return leg accrues from the last reset —
using the previous close instead would value one day's move as if it were
the whole period.

**Financing accrues on the notional, not on the current value.** An unfunded
TRS finances the original notional whatever the asset has since done, so a
position that has doubled does not owe twice the interest. Accruing on the
mark is a plausible-looking error that grows with the position's gain.
"""

from __future__ import annotations

from dataclasses import dataclass

from treble.analytics.registry import model


@dataclass(frozen=True)
class TotalReturnPricing:
    """A TRS mark-to-market, with both legs shown.

    Both legs are carried because their difference is usually small next to
    either one: a total return of +8% against financing of 3.4% nets to a
    number that a single field makes look like a small position rather than
    a large one held against a large liability.
    """

    value: float
    #: What the total return leg has accrued since the reset.
    return_leg: float
    #: What the financing leg has accrued since the reset.
    funding_leg: float
    #: Price return alone, carried apart from income so the two are never
    #: conflated on a screen.
    price_return: float
    income_return: float


@model(
    model_id="derivatives.total_return_swap",
    version="1.0",
    spec_section="§12.1",
    summary="Total return swap mark-to-market between reset dates",
)
def total_return_swap(
    *,
    notional: float,
    reset_price: float,
    current_price: float,
    income: float,
    funding_rate: float,
    spread: float,
    accrual: float,
    discount_factor: float,
    receive_return: bool = True,
) -> TotalReturnPricing:
    """Value a TRS between resets, from the total-return receiver's side.

    `income` is per unit of the asset, in the same units as the prices, and
    covers the period since the reset. Pass `0.0` deliberately when marking
    against an already-total-return series such as `ADJ_CLOSE`, or the
    dividends are counted twice.

    `accrual` is the year fraction since the reset on the funding leg's own
    day count, and `discount_factor` discounts the payment date to today.
    """
    if notional <= 0.0:
        raise ValueError("a notional of zero or less is not a position, it is a bad input")
    if reset_price <= 0.0:
        raise ValueError(
            "a reset price of zero or less gives no base for the return; this is a data "
            "error rather than an asset that was worthless at the reset"
        )
    if accrual < 0.0:
        raise ValueError("a negative accrual runs the financing leg backwards in time")

    units = notional / reset_price
    price_return = units * (current_price - reset_price)
    income_return = units * income
    # Financing accrues on the original notional, not on the current mark:
    # an unfunded TRS finances what was borrowed, whatever the asset did.
    funding = notional * (funding_rate + spread) * accrual

    gross = price_return + income_return
    net = (gross - funding) * discount_factor
    return TotalReturnPricing(
        value=net if receive_return else -net,
        return_leg=gross * discount_factor,
        funding_leg=funding * discount_factor,
        price_return=price_return * discount_factor,
        income_return=income_return * discount_factor,
    )


__all__ = ["TotalReturnPricing", "total_return_swap"]
