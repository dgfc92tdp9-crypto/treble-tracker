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
Caps and CMS need a volatility, inflation needs a projected index,
cross-currency needs a basis. None of those is in the store and none is
invented: each is a required argument, so the assumption sits with the
caller who made it rather than inside a number that looks measured.

**Six of the seven price from stored data, and that took correcting five
claims that they could not.** capfloor, cms, cancellable, assetswap,
inflation and crosscurrency were each recorded here as blocked on data —
a swaption vol, a bond price, an FX rate, an index, a basis — and in every
case the data was already in the store or the missing piece was an
assumption a caller states. The entries were written without checking.
:data:`UNFED_PRODUCTS` now holds one product, and the discipline that
matters is not what it says but that each line was verified before it was
believed.

`totalreturn` is the one that survived: it needs a *trade*, a reset price
and financing spread that belong to a position rather than to a market,
and this repository has no position store. That is a different kind of
absence from a missing quote.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from treble.analytics.derivatives.assetswap import AssetSwapPricing, asset_swap_spread
from treble.analytics.derivatives.cancellable import CancellablePricing, cancellable_swap
from treble.analytics.derivatives.capfloor import Caplet, price_cap, price_floor
from treble.analytics.derivatives.cms import cms_rate
from treble.analytics.derivatives.crosscurrency import (
    CrossCurrencyPricing,
    CrossCurrencySpec,
    price_cross_currency_swap,
)
from treble.analytics.derivatives.inflation import (
    InflationSwapPricing,
    InflationSwapSpec,
    price_inflation_swap,
)
from treble.analytics.holdings.implied_price import AssetCategory, implied_price
from treble.analytics.vol.surface import EXPIRY_BUCKETS, TENOR_BUCKETS, VolSurface
from treble.core.identifiers import TUID
from treble.ingest.ecb_hicp import SUBJECT as HICP_SUBJECT
from treble.store.duck import DuckStore
from treble.tapi.swap_market import (
    DISCOUNT_CURVE,
    SwapMarket,
    build_swap_market,
    build_usd_discount_curve,
)
from treble.tapi.vol_surface import build_vol_surface

#: Products no stored source can feed. One, and it is the only one of the
#: seven whose gap is a *position* rather than a market input: a total
#: return swap needs the trade's own reset price and financing spread.
#:
#: The other six were listed here at various points and every listing was
#: wrong. `unfed_reason` is what a screen calls before offering a product,
#: so an entry here removes a capability from a user — which is why the
#: bar for adding one is a measurement rather than an impression.
UNFED_PRODUCTS: dict[str, str] = {
    "totalreturn": "needs a reset price and financing spread from a trade, not a curve",
}


#: How far the prints behind a surface node may disagree, as a fraction
#: of the node's own level, before a product priced off it is refused.
#:
#: Distinct from `VolNode.is_confident`, which counts effective prints.
#: A node can be thick and incoherent: measured on the live surface,
#: 0.25y-into-2y holds 26 prints at 117% dispersion. Both faults have to
#: be checked, because each is invisible to the other's test.
MAX_NODE_DISPERSION = 0.60


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


@dataclass(frozen=True)
class CancellablePriced:
    """A cancellable swap and the surface node its option came from."""

    pricing: CancellablePricing
    #: The grid point the volatility was taken from, and how much the
    #: prints behind it disagreed. A cancellable priced off a node whose
    #: observations spanned 117% of their own median is a different claim
    #: from one priced off a node at 0%, and a single value cannot say so.
    expiry_years: float
    tenor_years: float
    volatility_bp: float
    node_dispersion: float
    node_observations: int


def _nearest(value: float, buckets: tuple[float, ...]) -> float:
    return min(buckets, key=lambda b: abs(b - value))


def cancellable_from_store(
    store: object,
    *,
    as_of: datetime,
    vanilla_value: float,
    notional: float,
    strike: float,
    expiry_years: float,
    tenor_years: float,
    payer: bool = True,
    require_confident: bool = True,
    max_dispersion: float = MAX_NODE_DISPERSION,
) -> CancellablePriced:
    """Price a cancellable swap off the fitted swaption surface.

    Recorded as needing a volatility "no stored source supplies", which was
    wrong twice over: `tapi/vol_surface.py` fits one from DTCC prints, and
    on the live store it carries 44 nodes at 79% grid coverage from 1,462
    solved prints. The same error as the cross-currency entry -- asserting
    an absence without checking.

    **The node's dispersion travels with the price, and a thin node is
    refused by default.** Surface quality is not uniform: measured, nodes
    range from 0% to 117% dispersion, and 11 of 44 fail `is_confident`.
    Pricing an option off a node whose prints disagreed by more than their
    own median, without saying so, would put that noise into a number the
    caller reads as a market price.
    """
    surface: VolSurface = build_vol_surface(store, as_of=as_of).surface  # type: ignore[arg-type]
    grid_expiry = _nearest(expiry_years, EXPIRY_BUCKETS)
    grid_tenor = _nearest(tenor_years, TENOR_BUCKETS)
    node = surface.at(grid_expiry, grid_tenor)
    if node is None:
        raise ProductUnavailableError(
            f"the surface has no node at {grid_expiry}y into {grid_tenor}y. It does not "
            "interpolate: a node exists where trades happened, and inventing one would "
            "be indistinguishable on screen from an observed level"
        )
    if require_confident and not node.is_confident:
        raise ProductUnavailableError(
            f"the {grid_expiry}y into {grid_tenor}y node rests on "
            f"{node.effective_observations:.1f} effective prints, below the "
            "confidence bar. Pass require_confident=False to price off it anyway"
        )
    # Thinness and disagreement are different faults and `is_confident`
    # only measures the first. On the live surface the 0.25y-into-2y node
    # carries 26 prints -- comfortably confident -- that disagree by 117%
    # of their own median. Twenty-six prints that contradict each other are
    # still twenty-six prints, and a guard that only counted them would let
    # that through while a docstring claimed otherwise.
    if require_confident and node.dispersion > max_dispersion:
        raise ProductUnavailableError(
            f"the {grid_expiry}y into {grid_tenor}y node has {node.dispersion:.0%} "
            f"dispersion across {node.observations} prints, above the "
            f"{max_dispersion:.0%} bar. The prints behind it disagree by more than "
            "the level itself; pass require_confident=False to price off it anyway"
        )

    market = build_swap_market(store, as_of=as_of)  # type: ignore[arg-type]
    curve = market.curves.curve(DISCOUNT_CURVE)
    step = 1.0 / _CMS_FREQUENCY
    start, end = expiry_years, expiry_years + tenor_years
    annuity = sum(step * curve.discount(t) for t in _payment_times(start, end))
    if annuity <= 0.0:
        raise ProductUnavailableError("the stored curve gives no annuity for that tenor")
    df_start, df_end = curve.discount(start), curve.discount(end)
    forward = (df_start - df_end) / annuity

    # The annuity above is per unit of notional; `vanilla_value` is in
    # currency. Passing them together subtracts a per-unit option from a
    # currency PV, which is dimensionally wrong and prices every
    # cancellation right at approximately nothing -- a cancellable that
    # costs the holder 4 cents against a 1mm swap, which reads as "the
    # option is worthless" rather than as a units mistake. Caught by
    # pricing one against the live surface and looking at the number.
    priced = cancellable_swap.__wrapped__(  # type: ignore[attr-defined]
        vanilla_value=vanilla_value,
        forward=forward,
        strike=strike,
        expiry_years=expiry_years,
        volatility=node.volatility,
        annuity=annuity * notional,
        payer=payer,
        normal_vol=True,
    )
    return CancellablePriced(
        pricing=priced,
        expiry_years=grid_expiry,
        tenor_years=grid_tenor,
        volatility_bp=node.volatility * 1e4,
        node_dispersion=node.dispersion,
        node_observations=node.observations,
    )


@dataclass(frozen=True)
class AssetSwapPriced:
    """An asset swap spread and the holding the price came from."""

    spread: AssetSwapPricing
    identifier: str
    #: Price implied from the filer's own valUSD and par balance, per 100.
    #: Carried because it is a *derived* mark rather than a traded level,
    #: and a spread computed off an implied price is a weaker claim than one
    #: off a print. §15 says the drill-down states what it rests on.
    implied_price: float
    coupon: float
    swap_rate: float
    maturity: str


def assetswap_from_store(store: DuckStore, *, as_of: datetime, subject: TUID) -> AssetSwapPriced:
    """The par-par asset swap spread for one stored bond.

    Recorded as needing "a bond price" no stored source supplies. Wrong, and
    the third such entry to be wrong the same way: the store holds 460 bonds
    with maturity, coupon, par balance and USD value, and
    `analytics/holdings/implied_price.py` turns the last two into a price.
    Both that module and `derivatives/assetswap.py` were orphaned; this is
    the caller for each.

    **The price is implied, not traded, and the result says so.** A filer's
    valUSD over par is a mark somebody reported at a quarter end, not a
    level anyone dealt at. A spread built on it is a weaker claim than one
    built on a print, and collapsing the two would let a quarterly
    accounting mark be read as a market spread.
    """
    facts = {f.field: f.value for f in store.subject_facts(subject, as_of=as_of)}
    maturity = facts.get("nport:maturityDt")
    coupon = facts.get("nport:annualizedRt")
    balance = facts.get("nport:balance")
    val_usd = facts.get("nport:valUSD")
    if not isinstance(maturity, date) or not isinstance(coupon, int | float):
        raise ProductUnavailableError(
            f"{subject}: no maturity or coupon stored, so there is no bond to swap"
        )
    if not isinstance(balance, int | float) or not isinstance(val_usd, int | float):
        raise ProductUnavailableError(
            f"{subject}: no par balance and value, so no price can be implied"
        )
    # `valUSD` is in USD; `balance` is par in the instrument's own currency.
    # Dividing one by the other across currencies scales the price by the FX
    # rate, and the result looks like a bond rather than like an error.
    #
    # Measured: the only stored bond this priced before the guard was
    # isin:AU3CB0328482, an Australian issuer with no reported currency --
    # 100,000 par against 68,836 USD, implying 68.84. At an AUD/USD near
    # 0.67 the real level is about 103. A distressed price and a currency
    # mistake render identically, and only one of them is a fact.
    currency = facts.get("nport:curCd")
    if currency != "USD":
        raise ProductUnavailableError(
            f"{subject}: reported currency is {currency!r}, and valUSD over a par "
            "balance in another currency is a price scaled by the FX rate. Refused "
            "rather than converted: this holds no FX rate for the filing's own date"
        )

    price: float = implied_price.__wrapped__(  # type: ignore[attr-defined]
        float(balance), float(val_usd), AssetCategory.DEBT
    )
    market = build_swap_market(store, as_of=as_of)
    curve = market.curves.curve(DISCOUNT_CURVE)
    years = (maturity - market.report_date).days / 365.25
    if years <= 0.0:
        raise ProductUnavailableError(
            f"{subject}: matures on or before the curve date, so there is nothing left to swap"
        )
    step = 1.0 / _CMS_FREQUENCY
    annuity = sum(step * curve.discount(t) for t in _payment_times(0.0, years))
    if annuity <= 0.0:
        raise ProductUnavailableError(f"{subject}: the curve gives no annuity to that maturity")
    swap_rate = (1.0 - curve.discount(years)) / annuity

    spread = asset_swap_spread.__wrapped__(  # type: ignore[attr-defined]
        price=price,
        coupon=float(coupon) / 100.0 if float(coupon) > 1.0 else float(coupon),
        swap_rate=swap_rate,
        annuity=annuity,
    )
    return AssetSwapPriced(
        spread=spread,
        identifier=str(subject),
        implied_price=price,
        coupon=float(coupon),
        swap_rate=swap_rate,
        maturity=maturity.isoformat(),
    )


def inflation_from_store(
    store: DuckStore,
    *,
    as_of: datetime,
    fixed_rate: float,
    maturity_years: float,
    projected_index: float,
    notional: float = 1e7,
    index_lag_months: int = 3,
    discount_factor: float = 1.0,
) -> InflationSwapPricing:
    """Price a zero-coupon inflation swap off the stored HICP base index.

    Recorded as blocked because the store holds no *projected* index at
    maturity. That was the same reasoning error made about cross-currency
    and about cancellable: the store holds no swaption volatility either,
    and no cross-currency basis, and both are caller inputs here rather
    than blockers. A projection is an assumption, and this module's rule is
    that assumptions are stated by the caller rather than defaulted.

    What the store *does* supply is the base index the contract fixed at,
    and that is the half a repository can be authoritative about.

    **The base index is read at the contract's lag, not at today.** A
    three-month lag means a trade struck now references the figure for
    three months ago, and reading the latest published level instead would
    measure a different period from the one the contract pays on.
    """
    lagged = as_of.date() - timedelta(days=round(index_lag_months * 30.44))
    # `store.read` for the series, not `subject_facts`: the latter returns
    # the latest fact per field, so a monthly index collapses to whichever
    # month is newest and every lagged read fails. The series API is what
    # the factor model uses for the same reason.
    facts = [
        f for f in store.read(HICP_SUBJECT, "PX_LAST", as_of=as_of) if f.effective_from <= lagged
    ]
    if not facts:
        raise ProductUnavailableError(
            f"no HICP observation on or before {lagged} (a {index_lag_months}-month lag "
            "from today). Run the ECB HICP adapter; the index is published monthly and "
            "in arrears"
        )
    raw = max(facts, key=lambda f: f.effective_from).value
    if not isinstance(raw, int | float):
        raise ProductUnavailableError(
            f"the HICP observation for {lagged} is not numeric; an index that cannot be "
            "read as a level cannot start a ratio"
        )
    base = float(raw)

    spec = InflationSwapSpec(
        notional=notional,
        fixed_rate=fixed_rate,
        maturity_years=maturity_years,
        index_lag_months=index_lag_months,
    )
    priced: InflationSwapPricing = price_inflation_swap.__wrapped__(  # type: ignore[attr-defined]
        spec,
        base_index=base,
        projected_index=projected_index,
        discount_factor=discount_factor,
    )
    return priced


#: The ECB publishes its reference rates with the euro as base, so
#: `fx:USDEUR` is *USD per EUR* — 1.1485 means one euro buys that many
#: dollars. `price_cross_currency_swap` wants domestic units per foreign
#: unit, and with EUR domestic against a USD leg that is the reciprocal.
#:
#: Named and inverted in one place rather than at each call site. A spot
#: rate used the wrong way up produces a value that is wrong by the square
#: of the rate and looks entirely ordinary.
FX_SUBJECT = TUID("fx:USDEUR")


def crosscurrency_from_store(
    store: DuckStore,
    *,
    as_of: datetime,
    domestic_notional: float,
    maturity_years: float,
    basis_spread: float,
    receive_domestic: bool = True,
) -> CrossCurrencyPricing:
    """A EUR/USD cross-currency swap off both stored curves.

    The euro leg discounts on EUR-ESTR-OIS and the dollar leg on
    USD-SOFR-OIS, each on its own calendar. `basis_spread` is required, the
    way volatility is for caps: no cross-currency basis is ingested, and a
    basis of zero is a claim rather than a default on an instrument whose
    entire purpose is that it is not zero.

    The foreign notional is struck at spot, which is what makes the trade
    par at inception — a notional chosen any other way would put a value on
    a swap nobody has entered yet.
    """
    market = build_swap_market(store, as_of=as_of)
    domestic = market.curves.curve(DISCOUNT_CURVE)
    foreign = build_usd_discount_curve(store, as_of=as_of, report_date=market.report_date)

    quotes = store.read(FX_SUBJECT, "PX_LAST", as_of=as_of)
    usable = [q for q in quotes if q.effective_from <= market.report_date]
    if not usable:
        raise ProductUnavailableError(
            f"no {FX_SUBJECT} fixing on or before {market.report_date}. The legs would "
            "have to be converted at a rate from a different day than they are "
            "discounted to"
        )
    raw = max(usable, key=lambda q: q.effective_from).value
    if not isinstance(raw, int | float) or float(raw) <= 0.0:
        raise ProductUnavailableError(f"{FX_SUBJECT} is not a usable rate")
    spot = 1.0 / float(raw)  # EUR per USD, from the ECB's USD per EUR

    # A par floating leg pays 1 - DF(T) in coupons per unit of notional;
    # the notional itself is the DF(T) term the pricer takes separately.
    domestic_df = domestic.discount(maturity_years)
    foreign_df = foreign.discount(maturity_years)
    step = 1.0 / _CMS_FREQUENCY
    foreign_annuity = sum(step * foreign.discount(t) for t in _payment_times(0.0, maturity_years))
    if foreign_annuity <= 0.0:
        raise ProductUnavailableError("the USD curve gives no annuity to that maturity")

    spec = CrossCurrencySpec(
        domestic_notional=domestic_notional,
        foreign_notional=domestic_notional / spot,
        basis_spread=basis_spread,
    )
    priced: CrossCurrencyPricing = price_cross_currency_swap.__wrapped__(  # type: ignore[attr-defined]
        spec,
        spot=spot,
        domestic_float_pv=1.0 - domestic_df,
        foreign_float_pv=1.0 - foreign_df,
        domestic_final_df=domestic_df,
        foreign_final_df=foreign_df,
        foreign_annuity=foreign_annuity,
        receive_domestic=receive_domestic,
    )
    return priced


__all__ = [
    "FX_SUBJECT",
    "MAX_NODE_DISPERSION",
    "UNFED_PRODUCTS",
    "AssetSwapPriced",
    "CancellablePriced",
    "CapPricing",
    "ProductUnavailableError",
    "assetswap_from_store",
    "cancellable_from_store",
    "cms_from_store",
    "crosscurrency_from_store",
    "inflation_from_store",
    "price_cap_from_store",
    "unfed_reason",
]
