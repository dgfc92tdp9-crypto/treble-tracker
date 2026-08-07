"""Par-par asset swaps (spec §12.1).

An asset swap packages a bond with an interest rate swap: the buyer pays par
for the bond, hands its fixed coupons to the swap counterparty, and receives
a floating rate plus a spread. That spread — the asset swap spread — is what
the package pays over the funding index, and it is the number a credit
investor compares across bonds whose coupons and prices differ.

**Par-par, and the name matters.** The buyer pays 100 regardless of what the
bond costs, so the difference `(100 - price)` is financed through the swap
and amortised over its life by the annuity. That is what makes this
comparable across a discount bond and a premium bond: the price difference
does not sit in an upfront payment where it would swamp the coupon
comparison, it is spread across the same schedule as everything else.

    spread = (100 - price) / annuity + (coupon - swap_rate)

**The two terms answer different questions and both belong.** The first is
what the bond's price says relative to par; the second is what its coupon
says relative to the market. A bond can be cheap on one and dear on the
other, and a spread built from only one of them would rank two bonds by an
accident of how their coupons were set at issue.

**This is the spread, not the credit.** An asset swap spread contains the
issuer's credit *and* the bond's own liquidity, its optionality if it has
any, and any richness in the swap curve it is measured against. `TVAL`'s
issuer curves are the place that separates those; this is the input to that
question rather than the answer to it.

**Not the same as a Z-spread or an OAS**, and the difference is not small
for a bond far from par. A Z-spread is a parallel shift to the zero curve
that reprices the bond; this is a spread on a floating leg alongside a par
purchase. They agree near par and diverge with the price difference, which
is exactly when somebody is most tempted to read one as the other.
"""

from __future__ import annotations

from dataclasses import dataclass

from treble.analytics.registry import model

#: Par, in the price convention this module takes. Bond prices arrive as a
#: percentage of face, so the price difference and the spread are in the
#: same units without a notional anywhere in the arithmetic.
PAR = 100.0


@dataclass(frozen=True)
class AssetSwapPricing:
    """An asset swap spread and the two things that make it up."""

    #: The spread, in basis points over the floating index.
    spread_bp: float
    #: Contribution of the bond's price relative to par, in basis points.
    price_bp: float
    #: Contribution of the coupon relative to the market swap rate, in bp.
    coupon_bp: float

    @property
    def dominated_by_price(self) -> bool:
        """Whether the price term carries more of the spread than the coupon.

        Carried because the two terms answer different questions. A spread
        that is mostly price is a statement about where the bond trades; one
        that is mostly coupon is a statement about how it was struck at
        issue, and a screen showing only the total cannot distinguish them.
        """
        return abs(self.price_bp) > abs(self.coupon_bp)


@model(
    model_id="derivatives.asset_swap_spread",
    version="1.0",
    spec_section="§12.1",
    summary="Par-par asset swap spread over the floating index",
)
def asset_swap_spread(
    *,
    price: float,
    coupon: float,
    swap_rate: float,
    annuity: float,
) -> AssetSwapPricing:
    """The par-par asset swap spread, in basis points.

    `price` is the bond's price as a percentage of face — 98.5, not 0.985.
    `coupon` and `swap_rate` are decimals. `annuity` is the swap's, from the
    same discount curve the swap is priced on, so the price difference is
    amortised on the schedule it is actually financed over.
    """
    if annuity <= 0.0:
        raise ValueError(
            "an annuity of zero or less cannot amortise the price difference; a swap "
            "with no remaining life is not an asset swap"
        )
    if price <= 0.0:
        raise ValueError("a bond price of zero or less is a data error, not a free bond")

    price_term = (PAR - price) / annuity / PAR
    coupon_term = coupon - swap_rate
    return AssetSwapPricing(
        spread_bp=(price_term + coupon_term) * 1e4,
        price_bp=price_term * 1e4,
        coupon_bp=coupon_term * 1e4,
    )


__all__ = ["PAR", "AssetSwapPricing", "asset_swap_spread"]
