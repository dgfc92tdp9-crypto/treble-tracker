"""§12.1 products priced off the stored curve environment (spec §12.1).

Seven pricers shipped and none was reachable. The reachability gate named
them: capfloor, cancellable, cms, crosscurrency, inflation, totalreturn,
assetswap — all correct, all mutation-checked, all called by nothing but
their own tests. This is the caller.

**One service, not seven screens.** The products differ in payoff and share
an environment: the same discount curve, the same forecast curve, the same
report date. A screen per product would build that environment seven times
and give seven chances for two of them to disagree about which day they
describe. `SWPM` gets a product tab instead.

**What each product needs beyond the curves is stated, not defaulted.**
Caps need a volatility, CMS needs one too, inflation needs an index level
and its lag, cross-currency needs a spot rate and a basis. This repository
holds curves and a swaption surface; it holds no inflation index, no FX
basis. Rather than default those to something plausible and return a
number, the priced entry points require them and :data:`UNFED_PRODUCTS`
names the ones whose inputs no stored source supplies — so a screen can
show "no data for this product" instead of a confident price built on an
assumption nobody made.

That distinction is the whole reason this module is thin. The pricers are
correct; what varies is whether anything in the store can feed them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from treble.analytics.derivatives.capfloor import Caplet, price_cap, price_floor
from treble.analytics.derivatives.cms import cms_rate
from treble.tapi.swap_market import DISCOUNT_CURVE, SwapMarket, build_swap_market

#: Products whose inputs this repository has no stored source for. Named
#: rather than silently priced off a default: a cross-currency basis of
#: zero is not a neutral assumption, it is a claim that the basis is zero.
UNFED_PRODUCTS: dict[str, str] = {
    "inflation": (
        "ECB HICP is now ingested (ingest/ecb_hicp.py), so the index exists. What "
        "is still missing is the projected index at maturity: a breakeven curve, "
        "which no free source publishes and which this cannot invent from spot "
        "levels without assuming the very thing the swap prices"
    ),
    "crosscurrency": (
        "ECB supplies spot but no cross-currency basis is ingested, and a basis of "
        "zero is a claim rather than a default"
    ),
    "totalreturn": "needs a reset price and financing spread from a trade, not a curve",
    "assetswap": "needs a bond price; YAS already computes the spread where one exists",
    "cancellable": "needs a swaption volatility for the cancellation right",
}


class ProductUnavailableError(RuntimeError):
    """This product cannot be priced from what is stored, with the reason."""


@dataclass(frozen=True)
class CapPricing:
    """A cap or floor priced off the stored curves."""

    value: float
    caplets: int
    strike: float
    volatility: float
    report_date: str
    #: Normal vol, since the curve environment here is EUR.
    normal_vol: bool = True


def _strip(market: SwapMarket, *, years: float, frequency: int) -> list[Caplet]:
    """A quarterly strip out to `years`, from the stored discount curve.

    Forwards come from the discount curve's own implied rates rather than a
    flat assumption, so a cap priced here moves with the curve the rest of
    `SWPM` is priced on. Using a constant forward would make the cap agree
    with nothing else on the screen.
    """
    curve = market.curves.curve(DISCOUNT_CURVE)
    caplets: list[Caplet] = []
    step = 1.0 / frequency
    periods = max(round(years * frequency), 1)
    for n in range(periods):
        start = step * (n + 1)
        pay = start + step
        df_start, df_pay = curve.discount(start), curve.discount(pay)
        if df_start <= 0.0 or df_pay <= 0.0:
            continue
        forward = (df_start / df_pay - 1.0) / step
        caplets.append(
            Caplet(
                forward=forward,
                accrual=step,
                discount_factor=df_pay,
                expiry_years=start,
            )
        )
    if not caplets:
        raise ProductUnavailableError(
            f"the stored curve produced no usable forwards out to {years} years"
        )
    return caplets


def price_cap_from_store(
    store: object,
    *,
    as_of: datetime,
    strike: float,
    volatility: float,
    years: float = 5.0,
    frequency: int = 4,
    floor: bool = False,
) -> CapPricing:
    """Price a cap or floor on the stored curve environment.

    `volatility` is a normal vol, in decimal, and is required. The swaption
    surface this repository fits carries median node dispersion of 55%, so
    taking a cap vol from it silently would hand that noise to a number the
    caller thinks came from the market. Stating it keeps the assumption
    where the caller can see it.
    """
    market = build_swap_market(store, as_of=as_of)  # type: ignore[arg-type]
    caplets = _strip(market, years=years, frequency=frequency)
    pricer = price_floor if floor else price_cap
    priced = pricer.__wrapped__(  # type: ignore[attr-defined]
        caplets, strike=strike, volatility=volatility, normal_vol=True
    )
    return CapPricing(
        value=priced.value,
        caplets=len(priced.caplets),
        strike=strike,
        volatility=volatility,
        report_date=market.report_date.isoformat(),
    )


def cms_from_store(
    store: object,
    *,
    as_of: datetime,
    tenor_years: float,
    expiry_years: float,
    volatility: float,
) -> dict[str, float | str]:
    """The convexity-adjusted CMS rate, off the stored forward swap rate.

    `volatility` is a *lognormal* vol, because Hull's expansion is derived
    under one. Required for the same reason the cap's is.
    """
    market = build_swap_market(store, as_of=as_of)  # type: ignore[arg-type]
    curve = market.curves.curve(DISCOUNT_CURVE)
    start, end = expiry_years, expiry_years + tenor_years
    df_start, df_end = curve.discount(start), curve.discount(end)
    # Each payment accrues over its own period, so the annuity is the sum
    # of `step * DF`, not of DF. Omitting the step inflates the annuity by
    # the frequency and halves the forward -- 1.73% against a curve sitting
    # at 3%, which is what the test caught.
    step = 1.0 / _CMS_FREQUENCY
    annuity = sum(step * curve.discount(t) for t in _payment_times(start, end))
    if annuity <= 0.0 or df_start <= 0.0:
        raise ProductUnavailableError("the stored curve gives no annuity for that tenor")
    forward = (df_start - df_end) / annuity
    adjusted = cms_rate.__wrapped__(  # type: ignore[attr-defined]
        forward_swap_rate=forward,
        volatility=volatility,
        expiry_years=expiry_years,
        tenor_years=tenor_years,
    )
    return {
        "forward_swap_rate": adjusted.forward_swap_rate,
        "cms_rate": adjusted.rate,
        "adjustment_bp": adjusted.adjustment_bp,
        "report_date": market.report_date.isoformat(),
    }


#: Payment frequency of the underlying swap the CMS rate is on.
_CMS_FREQUENCY = 2


def _payment_times(start: float, end: float, frequency: int = _CMS_FREQUENCY) -> list[float]:
    step = 1.0 / frequency
    times: list[float] = []
    t = start + step
    while t <= end + 1e-9:
        times.append(t)
        t += step
    return times


def unfed_reason(product: str) -> str | None:
    """Why a product cannot be priced from the store, or `None`.

    A screen calls this before offering a product, so a user meets "no
    inflation index is ingested" rather than a field of dashes.
    """
    return UNFED_PRODUCTS.get(product)


__all__ = [
    "UNFED_PRODUCTS",
    "CapPricing",
    "ProductUnavailableError",
    "cms_from_store",
    "price_cap_from_store",
    "unfed_reason",
]
