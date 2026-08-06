"""Tenor basis swaps — float versus float (spec §12.1).

A basis swap exchanges two floating legs on the same currency: 3M EURIBOR
plus a spread against 6M EURIBOR. The spread is the price, and it is what
the tenor basis curves were built to express — a 3M curve and a 6M curve
differ precisely because the market charges for the tenor.

**The legs are the vanilla pricer's legs.** Each side is built by asking
`swap` for the floating leg of a swap on that curve, rather than by
reimplementing projection here. A basis swap whose legs were computed
independently could disagree with the vanilla swap about the same 6M
EURIBOR cash flows, and the disagreement would be small enough to look like
rounding.

**The spread sits on the leg the trade says.** Market convention puts it on
the shorter tenor, but convention is not arithmetic: which leg carries the
spread changes its sign and its annuity. The trade states the leg and the
sign follows from that rather than from an assumption here.

**Both legs discount on the CSA curve, not on their own.** Two floating legs
each discounted on their own forecast curve would both be worth
`N x (D(0) - D(T))` on that curve and the basis would collapse to nearly
nothing — which is exactly the single-curve error the multi-curve framework
exists to avoid.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from treble.analytics._ql import DayCount, Market
from treble.analytics.curves.multicurve import CurveSet
from treble.analytics.derivatives.csa import CsaTerms
from treble.analytics.derivatives.swap import Cashflow, SwapPricingError, SwapSpec, _float_flows
from treble.analytics.registry import model


class BasisSwapSpec(BaseModel):
    """A float-versus-float swap on one currency, two index tenors."""

    model_config = ConfigDict(frozen=True)

    notional: float = Field(gt=0.0)
    effective: date
    maturity: date
    #: The leg this book pays, and the one carrying the spread.
    pay_curve: str
    #: The leg this book receives.
    receive_curve: str
    #: Contractual spread on the *pay* leg, e.g. 0.0012 for +12bp.
    pay_spread: float = 0.0
    pay_day_count: DayCount = DayCount.ACT_360
    receive_day_count: DayCount = DayCount.ACT_360
    #: Matches `SwapSpec`'s default deliberately. A basis swap and a
    #: vanilla swap built with default calendars must schedule the same
    #: way, or the same 6M leg is worth different amounts in the two and
    #: the difference reads as a modelling error rather than as a
    #: different holiday calendar.
    calendar: Market = Market.US_SETTLEMENT
    #: Payment frequency per leg. `None` takes it from the curve's index
    #: tenor, which is what a term index means; an overnight leg has no
    #: tenor and must state one, exactly as a vanilla OIS leg does.
    pay_frequency: int | None = Field(default=None, gt=0)
    receive_frequency: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _distinct_legs(self) -> BasisSwapSpec:
        if self.maturity <= self.effective:
            raise ValueError("a swap matures after it starts")
        if self.pay_curve == self.receive_curve and self.pay_spread == 0.0:
            # Legal, and almost certainly a mistake: the trade is worth zero
            # by construction. Allowed because it is the identity test any
            # basis engine should pass, and flagged nowhere else.
            pass
        return self


class BasisPricing(BaseModel):
    """A valued basis swap and the environment that valued it."""

    model_config = ConfigDict(frozen=True)

    pv: float
    pay_leg_pv: float
    receive_leg_pv: float
    #: PV01 of the spread: what one basis point on the pay leg is worth.
    #: Named separately from a swap's DV01 because it moves with the spread,
    #: not with the level of rates.
    spread_annuity: float
    #: The spread on the pay leg that makes the trade worth zero.
    par_spread: float
    discount_curve: str
    pay_curve: str
    receive_curve: str
    #: The two legs, kept apart. Both are floating, so merging them into one
    #: schedule would leave every row labelled "float" with no way to tell
    #: which side it is — and a basis swap's whole content is the difference
    #: between the two sides.
    pay_cashflows: tuple[Cashflow, ...]
    receive_cashflows: tuple[Cashflow, ...]

    @property
    def par_spread_bp(self) -> float:
        return self.par_spread * 1e4


def _leg(
    spec: BasisSwapSpec,
    curve_name: str,
    frequency: int | None,
    day_count: DayCount,
    spread: float,
    curves: CurveSet,
    discount_name: str,
) -> tuple[Cashflow, ...]:
    """One floating leg, built by the vanilla pricer.

    Going through `SwapSpec` rather than projecting here is the point: the
    same 6M EURIBOR cash flows must be worth the same in a basis swap and in
    a vanilla swap, and two implementations would eventually disagree by an
    amount small enough to read as rounding.
    """
    proxy = SwapSpec(
        notional=spec.notional,
        fixed_rate=0.0,
        effective=spec.effective,
        maturity=spec.maturity,
        forecast_curve=curve_name,
        float_day_count=day_count,
        float_spread=spread,
        float_frequency=frequency,
        calendar=spec.calendar,
    )
    forecast = curves.curve(curve_name)
    if forecast.config.index_tenor is None and frequency is None:
        raise SwapPricingError(
            f"{curve_name!r} forecasts an overnight index, which has no tenor to schedule "
            "from. State the frequency for that leg"
        )
    return _float_flows(proxy, curves.curve(discount_name), forecast)


@model(
    model_id="derivatives.price_basis_swap",
    version="1.0",
    spec_section="§12.1",
    summary="Tenor basis swap PV and par spread, both legs on the CSA discount curve",
)
def price_basis_swap(spec: BasisSwapSpec, curves: CurveSet, csa: CsaTerms) -> BasisPricing:
    """Value a float-float basis swap and solve its par spread."""
    discount = csa.resolve(curves)
    discount_name = discount.config.name
    if spec.effective < curves.as_of:
        raise SwapPricingError(
            f"the swap started {spec.effective} and the curves are as of {curves.as_of}: "
            "the first floating period on each leg has already fixed, and projecting it "
            "would invent rates that were in fact published"
        )

    pay = _leg(
        spec,
        spec.pay_curve,
        spec.pay_frequency,
        spec.pay_day_count,
        spec.pay_spread,
        curves,
        discount_name,
    )
    receive = _leg(
        spec,
        spec.receive_curve,
        spec.receive_frequency,
        spec.receive_day_count,
        0.0,
        curves,
        discount_name,
    )
    pay_pv = sum(flow.present_value for flow in pay)
    receive_pv = sum(flow.present_value for flow in receive)

    # The annuity of the spread: one basis point on the pay leg is worth
    # this, and it is what turns a PV difference into a spread.
    spread_annuity = sum(flow.notional * flow.accrual * flow.discount_factor for flow in pay)
    if spread_annuity <= 0.0:
        raise SwapPricingError(
            "the pay leg has no annuity, so no spread on it can make the trade worth "
            "zero and a par spread does not exist"
        )
    # PV = receive - pay, and pay already carries `pay_spread`. Solving for
    # the spread that zeroes it means adding back what is there.
    par_spread = spec.pay_spread + (receive_pv - pay_pv) / spread_annuity

    return BasisPricing(
        pv=receive_pv - pay_pv,
        pay_leg_pv=pay_pv,
        receive_leg_pv=receive_pv,
        spread_annuity=spread_annuity,
        par_spread=par_spread,
        discount_curve=discount_name,
        pay_curve=spec.pay_curve,
        receive_curve=spec.receive_curve,
        pay_cashflows=pay,
        receive_cashflows=receive,
    )


@model(
    model_id="derivatives.basis_par_spread",
    version="1.0",
    spec_section="§12.1",
    summary="The pay-leg spread that makes a tenor basis swap worth zero",
)
def basis_par_spread(spec: BasisSwapSpec, curves: CurveSet, csa: CsaTerms) -> float:
    priced: BasisPricing = price_basis_swap.__wrapped__(spec, curves, csa)  # type: ignore[attr-defined]
    return priced.par_spread


__all__ = ["BasisPricing", "BasisSwapSpec", "basis_par_spread", "price_basis_swap"]
