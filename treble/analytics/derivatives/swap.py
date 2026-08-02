"""`SWPM` — the swap manager (spec §12.1, Phase 2 gate).

    For each: PV, par rate, DV01 and bucketed DV01s, cash flow schedules,
    and CSA-aware discounting.

A vanilla interest rate swap is arithmetic anyone can write in twenty lines.
Getting it *right* is entirely about which curve each term comes from, and
every way of getting it wrong produces a plausible number:

- **Discounting off the forecast curve.** The pre-2008 identity, in which
  the floating leg telescopes to ``D(t_0) - D(t_n)`` and the forwards drop
  out. Under a real basis it misprices a long-dated swap by tens of basis
  points of PV, silently. Here the two curves arrive from different places
  and cannot be the same object by accident: the discount curve comes from
  the CSA, the forecast curve is named on the trade.
- **Reading the float schedule off the trade instead of the curve.** A 3M
  index projected on a semiannual schedule takes every forward over the
  wrong period. The float frequency is therefore derived from the
  forecasting curve's index tenor and is not a field of the trade.
- **Bumping solved zeros to get DV01.** Shifting a curve's zeros produces a
  curve that reprices none of its inputs, and then reports the sensitivity
  of that. Risk here rebuilds the curve set from bumped *market quotes*.
- **Pricing a seasoned swap without its fixings.** A period that began
  before the curve date has already fixed; projecting it forward invents a
  rate. Refused rather than projected.

The trade's day counts are its own — a USD fixed leg is 30/360 and the
floating leg ACT/360 — and neither is the curve's. Mixing them is a small
error that compounds across thirty years of coupons.
"""

from __future__ import annotations

from datetime import date
from itertools import pairwise
from typing import Literal

import QuantLib as ql
from pydantic import BaseModel, ConfigDict, Field, model_validator

from treble.analytics import _ql
from treble.analytics._ql import DayCount, Market
from treble.analytics.curves.bootstrap import Curve
from treble.analytics.curves.config import InstrumentKind
from treble.analytics.curves.multicurve import CurveSet
from treble.analytics.derivatives.csa import CsaTerms
from treble.analytics.registry import model


class SwapPricingError(ValueError):
    """The trade cannot be valued honestly from the inputs supplied."""


class NotionalStep(BaseModel):
    """A change in notional taking effect on a date (spec §12.1 amortising)."""

    model_config = ConfigDict(frozen=True)

    effective: date
    notional: float = Field(gt=0.0)


class SwapSpec(BaseModel):
    """A fixed-versus-index swap, as a trade ticket rather than as curves."""

    model_config = ConfigDict(frozen=True)

    notional: float = Field(gt=0.0)
    fixed_rate: float
    effective: date
    maturity: date
    #: The curve projecting the floating index. Named on the trade because
    #: the same cash flows off a 3M or a 6M curve are different trades.
    forecast_curve: str
    fixed_frequency: int = Field(default=1, gt=0)
    fixed_day_count: DayCount = DayCount.THIRTY_360
    float_day_count: DayCount = DayCount.ACT_360
    #: Contractual spread on the floating leg, e.g. 0.0025 for +25bp.
    float_spread: float = 0.0
    #: Whose PV. True = this book pays fixed and receives floating.
    pay_fixed: bool = True
    calendar: Market = Market.US_SETTLEMENT
    #: Notional steps, in date order. Empty means a constant notional.
    amortisation: tuple[NotionalStep, ...] = ()

    @model_validator(mode="after")
    def _coherent(self) -> SwapSpec:
        if self.maturity <= self.effective:
            raise ValueError("a swap matures after it starts")
        if 12 % self.fixed_frequency:
            raise ValueError(
                f"fixed frequency {self.fixed_frequency} does not divide the year into "
                "whole months, so it has no regular schedule"
            )
        dates = [step.effective for step in self.amortisation]
        if dates != sorted(dates) or len(set(dates)) != len(dates):
            raise ValueError("amortisation steps must be in strict date order")
        return self

    def notional_at(self, accrual_start: date) -> float:
        """The notional in force for a period beginning on this date.

        Read at the period *start*, which is how a schedule amortises: a
        step dated mid-period does not retroactively change a coupon that
        has already begun accruing.
        """
        current = self.notional
        for step in self.amortisation:
            if step.effective <= accrual_start:
                current = step.notional
        return current


class Cashflow(BaseModel):
    """One period of one leg, with everything that produced its PV."""

    model_config = ConfigDict(frozen=True)

    leg: Literal["fixed", "float"]
    accrual_start: date
    accrual_end: date
    notional: float
    #: Fixed coupon, or the projected forward plus the contractual spread.
    rate: float
    accrual: float
    amount: float
    discount_factor: float
    present_value: float


class SwapPricing(BaseModel):
    """A valued swap and the curve environment that valued it.

    The curve names and hashes are fields rather than metadata: "which
    curve discounted this" is the question a swap PV most often needs
    answered, and putting it beside the number means a reader does not have
    to trust that the right one was configured.
    """

    model_config = ConfigDict(frozen=True)

    pv: float
    fixed_leg_pv: float
    float_leg_pv: float
    #: The fixed rate that would make this trade settle at zero.
    par_rate: float
    #: PV of one unit of fixed rate on the notional schedule — the fixed
    #: leg's sensitivity to its own coupon.
    annuity: float
    cashflows: tuple[Cashflow, ...]
    discount_curve: str
    forecast_curve: str
    discount_curve_hash: str
    forecast_curve_hash: str
    csa: str


class BucketDv01(BaseModel):
    """Sensitivity to one quoted instrument, the unit a hedge is traded in."""

    model_config = ConfigDict(frozen=True)

    curve: str
    kind: InstrumentKind
    tenor: str
    dv01: float


def _schedule(spec: SwapSpec, frequency: int) -> tuple[date, ...]:
    """Accrual dates from effective to maturity at ``frequency`` per year."""
    cal = _ql.calendar(spec.calendar)
    schedule = ql.Schedule(
        _ql.to_ql_date(spec.effective),
        _ql.to_ql_date(spec.maturity),
        ql.Period(12 // frequency, ql.Months),
        cal,
        _ql.business_day(_ql.BusinessDay.MODIFIED_FOLLOWING),
        _ql.business_day(_ql.BusinessDay.MODIFIED_FOLLOWING),
        ql.DateGeneration.Backward,
        False,
    )
    return tuple(_ql.from_ql_date(d) for d in schedule)


def _accrual(day_count: DayCount, start: date, end: date) -> float:
    return float(
        _ql.day_counter(day_count).yearFraction(_ql.to_ql_date(start), _ql.to_ql_date(end))
    )


def _fixed_flows(spec: SwapSpec, discount: Curve) -> tuple[Cashflow, ...]:
    dates = _schedule(spec, spec.fixed_frequency)
    flows = []
    for start, end in pairwise(dates):
        notional = spec.notional_at(start)
        accrual = _accrual(spec.fixed_day_count, start, end)
        amount = notional * spec.fixed_rate * accrual
        discount_factor = discount.discount_at(end)
        flows.append(
            Cashflow(
                leg="fixed",
                accrual_start=start,
                accrual_end=end,
                notional=notional,
                rate=spec.fixed_rate,
                accrual=accrual,
                amount=amount,
                discount_factor=discount_factor,
                present_value=amount * discount_factor,
            )
        )
    return tuple(flows)


def _float_flows(spec: SwapSpec, discount: Curve, forecast: Curve) -> tuple[Cashflow, ...]:
    """Projected floating coupons: forwards from one curve, DFs from another.

    The forward for a period is ``(P(s)/P(e) - 1) / τ`` where ``P`` is the
    forecast curve's pseudo-discount factor and ``τ`` the leg's own accrual.
    Both times are converted by the forecast curve, so a difference in day
    count between the two curves cannot leak into the projection.
    """
    dates = _schedule(spec, forecast.config.index_frequency)
    flows = []
    for start, end in pairwise(dates):
        notional = spec.notional_at(start)
        accrual = _accrual(spec.float_day_count, start, end)
        growth = forecast.discount_at(start) / forecast.discount_at(end) - 1.0
        forward = growth / accrual
        rate = forward + spec.float_spread
        amount = notional * rate * accrual
        discount_factor = discount.discount_at(end)
        flows.append(
            Cashflow(
                leg="float",
                accrual_start=start,
                accrual_end=end,
                notional=notional,
                rate=rate,
                accrual=accrual,
                amount=amount,
                discount_factor=discount_factor,
                present_value=amount * discount_factor,
            )
        )
    return tuple(flows)


def _resolve_curves(spec: SwapSpec, curves: CurveSet, csa: CsaTerms) -> tuple[Curve, Curve]:
    """The discount curve (from the CSA) and the forecast curve (from the trade)."""
    discount = csa.resolve(curves)
    forecast = curves.curve(spec.forecast_curve)
    if forecast.config.index_tenor is None:
        raise SwapPricingError(
            f"{spec.forecast_curve!r} forecasts no index tenor. An overnight index "
            "compounds daily within each period; scheduling it as discrete index "
            "periods would value a different instrument, so OIS legs are refused "
            "rather than approximated"
        )
    if spec.effective < curves.as_of:
        raise SwapPricingError(
            f"the swap started {spec.effective} and the curves are as of {curves.as_of}: "
            "the first floating period has already fixed. Projecting it from the curve "
            "would invent a rate that was in fact published, so a seasoned trade needs "
            "its fixing history"
        )
    return discount, forecast


def _value(spec: SwapSpec, curves: CurveSet, csa: CsaTerms) -> SwapPricing:
    """The pricing core, callable without the I3 envelope.

    Risk measures reprice the same trade many times under bumped curve
    sets; wrapping each of those in an envelope would stamp model identity
    on intermediates rather than on the reported sensitivity.
    """
    discount, forecast = _resolve_curves(spec, curves, csa)
    fixed = _fixed_flows(spec, discount)
    floating = _float_flows(spec, discount, forecast)

    fixed_leg_pv = sum(f.present_value for f in fixed)
    float_leg_pv = sum(f.present_value for f in floating)
    annuity = sum(f.notional * f.accrual * f.discount_factor for f in fixed)
    if annuity <= 0.0:
        raise SwapPricingError("the fixed leg has no annuity, so no par rate exists")

    # PV is the position's, not the market's: a payer and a receiver of the
    # same trade hold opposite numbers, and a sign convention chosen by the
    # library rather than by the ticket is one nobody remembers.
    pv = float_leg_pv - fixed_leg_pv if spec.pay_fixed else fixed_leg_pv - float_leg_pv

    return SwapPricing(
        pv=pv,
        fixed_leg_pv=fixed_leg_pv,
        float_leg_pv=float_leg_pv,
        par_rate=float_leg_pv / annuity,
        annuity=annuity,
        cashflows=(*fixed, *floating),
        discount_curve=discount.config.name,
        forecast_curve=forecast.config.name,
        discount_curve_hash=discount.content_hash,
        forecast_curve_hash=forecast.content_hash,
        csa=csa.label,
    )


@model(
    model_id="derivatives.price_swap",
    version="1.0",
    spec_section="§12.1",
    summary="Multi-curve CSA-discounted swap PV, par rate and cash flows",
)
def price_swap(spec: SwapSpec, curves: CurveSet, csa: CsaTerms) -> SwapPricing:
    """Value a swap: forwards off the trade's curve, discounting off the CSA's.

    The envelope records the curve set's content hash automatically (the
    set exposes ``content_hash``), so the result identifies the entire
    curve environment and not merely the two curves it happened to read.
    """
    return _value(spec, curves, csa)


@model(
    model_id="derivatives.swap_par_rate",
    version="1.0",
    spec_section="§12.1",
    summary="The fixed rate at which a swap settles at zero PV",
)
def swap_par_rate(spec: SwapSpec, curves: CurveSet, csa: CsaTerms) -> float:
    """The break-even fixed rate for this trade under this CSA.

    Depends on the discounting, not only on the forwards: the par rate is a
    ratio of the floating leg's PV to the fixed annuity, and both are
    discounted. Two identical trades under different CSAs have different
    par rates, which is the clearest demonstration that the collateral
    agreement is part of the economics.
    """
    return _value(spec, curves, csa).par_rate


@model(
    model_id="derivatives.swap_dv01",
    version="1.0",
    spec_section="§12.1",
    summary="PV change per 1bp parallel bump, by rebuilding every curve",
)
def swap_dv01(spec: SwapSpec, curves: CurveSet, csa: CsaTerms) -> float:
    """Signed PV change for a 1bp rise in every quote in the curve set.

    Signed from the position's perspective rather than reported as a
    magnitude: a payer and a receiver have opposite exposures, and an
    absolute value hides which one this is.

    The bump moves *market quotes* and re-solves every curve. The forecast
    curve is rebuilt against the rebuilt discount curve, so the basis is
    held at its quoted level instead of being distorted by the shift —
    which is what shifting zeros on each curve independently would do.
    """
    base = _value(spec, curves, csa)
    bumped = _value(spec, curves.bumped(1.0), csa)
    return bumped.pv - base.pv


@model(
    model_id="derivatives.swap_bucketed_dv01",
    version="1.0",
    spec_section="§12.1",
    summary="PV change per 1bp on each quoted curve instrument",
)
def swap_bucketed_dv01(spec: SwapSpec, curves: CurveSet, csa: CsaTerms) -> tuple[BucketDv01, ...]:
    """Sensitivity to each curve instrument separately — the hedge ladder.

    One bucket per quoted instrument on every curve in the set, including
    the discount curve: a swap discounted at OIS has genuine OIS exposure,
    and a ladder that showed only the forecast curve would leave that
    unhedged while appearing complete.

    The buckets do not sum exactly to the parallel DV01. That is a property
    of the curve solve rather than an error — bumping one node re-solves
    the interpolation around it, and the cross terms are real.
    """
    base = _value(spec, curves, csa).pv
    buckets = []
    for curve_name in curves.names:
        for kind, tenor in curves.buckets(curve_name):
            bumped = curves.bumped(1.0, curve=curve_name, instrument=(kind, tenor))
            buckets.append(
                BucketDv01(
                    curve=curve_name,
                    kind=kind,
                    tenor=tenor,
                    dv01=_value(spec, bumped, csa).pv - base,
                )
            )
    return tuple(buckets)


__all__ = [
    "BucketDv01",
    "Cashflow",
    "NotionalStep",
    "SwapPricing",
    "SwapPricingError",
    "SwapSpec",
    "price_swap",
    "swap_bucketed_dv01",
    "swap_dv01",
    "swap_par_rate",
]
