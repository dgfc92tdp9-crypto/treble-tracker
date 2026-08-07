"""Zero-coupon inflation swaps (spec §12.1).

At maturity one side pays realised inflation over the life of the trade,
`I_T / I_0 - 1`, and the other pays a fixed compounded rate,
`(1 + k)^T - 1`. The fixed rate that makes the two equal is the breakeven,
and it is the number quoted.

**The index is lagged, and the lag is the convention that goes wrong.**
A price index is published weeks after the month it measures, so an
inflation swap never references the index on its own dates — it references
the index some fixed number of months earlier. US CPI swaps use a three-month
lag; UK RPI trades at two or eight months depending on the contract. A
valuation that used the index level on the trade's own dates would be
measuring a different period from the one the contract pays on, and would be
wrong by whatever inflation did in the intervening months. So
:class:`InflationSwapSpec` requires the lag rather than assuming one, and
carries it onto the result where a screen can show it.

**Compounded, not simple.** The fixed leg is `(1 + k)^T - 1`. Over one year
the difference from `k * T` is nothing; over thirty years at 3% it is the
difference between 143% and 90%, which is not a rounding matter. Zero-coupon
inflation swaps are long-dated instruments and this is where a simple-rate
shortcut does its damage.

**Deflation is not floored here.** Some inflation products embed a floor at
zero — index-linked gilts do not, US TIPS do at the principal — but a plain
zero-coupon swap does not, and adding one would price a different contract.
A negative realised inflation gives a negative return leg, and the receiver
pays.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from treble.analytics.registry import model


class InflationSwapSpec(BaseModel):
    """A zero-coupon inflation swap, as quoted."""

    model_config = ConfigDict(frozen=True)

    notional: float = Field(gt=0.0)
    #: Fixed compounded rate, e.g. 0.025 for a 2.5% breakeven.
    fixed_rate: float
    #: Years to maturity.
    maturity_years: float = Field(gt=0.0)
    #: Months the index is lagged by. Required, not defaulted: three for US
    #: CPI, two or eight for UK RPI depending on the contract, and a wrong
    #: lag measures a different period from the one the trade pays on.
    index_lag_months: int = Field(ge=0)


@dataclass(frozen=True)
class InflationSwapPricing:
    """A priced inflation swap, with both legs and the breakeven."""

    value: float
    #: Realised or projected index ratio less one, times notional.
    inflation_leg: float
    #: `(1 + k)^T - 1` times notional.
    fixed_leg: float
    #: The fixed rate that would make the two equal, given this index
    #: projection. What the market quotes.
    breakeven_rate: float
    #: Carried through so a screen can state which lag the number assumes.
    index_lag_months: int


@model(
    model_id="derivatives.zero_coupon_inflation_swap",
    version="1.0",
    spec_section="§12.1",
    summary="Zero-coupon inflation swap value and breakeven rate",
)
def price_inflation_swap(
    spec: InflationSwapSpec,
    *,
    base_index: float,
    projected_index: float,
    discount_factor: float,
    receive_inflation: bool = True,
) -> InflationSwapPricing:
    """Value a zero-coupon inflation swap and report its breakeven.

    `base_index` is the index level the contract fixed at, already lagged;
    `projected_index` is the level expected at maturity, on the same lagged
    basis. Both arrive lagged rather than being lagged here, because the
    lag applies to which *published* figure the contract names, and this
    module does not hold a publication calendar.
    """
    if base_index <= 0.0:
        raise ValueError(
            "a base index of zero or less gives no basis for a ratio; this is a data "
            "error rather than an economy with no prices"
        )
    if projected_index <= 0.0:
        raise ValueError("a projected index of zero or less is a data error")

    ratio = projected_index / base_index
    inflation = (ratio - 1.0) * spec.notional
    fixed = ((1.0 + spec.fixed_rate) ** spec.maturity_years - 1.0) * spec.notional
    net = (inflation - fixed) * discount_factor
    return InflationSwapPricing(
        value=net if receive_inflation else -net,
        inflation_leg=inflation * discount_factor,
        fixed_leg=fixed * discount_factor,
        # The rate that would have made this trade fair, compounded on the
        # same basis as the fixed leg it is compared against.
        breakeven_rate=ratio ** (1.0 / spec.maturity_years) - 1.0,
        index_lag_months=spec.index_lag_months,
    )


__all__ = ["InflationSwapPricing", "InflationSwapSpec", "price_inflation_swap"]
