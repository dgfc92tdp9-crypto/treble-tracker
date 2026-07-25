"""Callable bond analytics: YTC/YTW and lattice OAS (spec §10.1-10.2).

OAS follows ADR-0003: a Hull-White short-rate model on a trinomial lattice,
with volatility and mean reversion as **explicit user-supplied parameters**
stamped into the I3 envelope. VCUB calibration becomes the parameter source
at Phase 2; the engine is unchanged by that.

Effective duration/convexity re-run the full lattice under bumped curves
(spec §10.1: for callables this differs materially from modified duration,
and using the wrong one is a real hedging error).
"""

from __future__ import annotations

from datetime import date

import QuantLib as ql

from treble.analytics import _ql
from treble.analytics.bonds.pricing import _FREQ_TO_QL, yield_from_price
from treble.analytics.bonds.spec import FixedBondSpec
from treble.analytics.curves.bootstrap import Curve
from treble.analytics.registry import model

_LATTICE_STEPS = 400


@model(
    model_id="bonds.yield_to_call",
    version="1.0",
    spec_section="§10.1",
    summary="Yield to a specific call date/price",
)
def yield_to_call(
    spec: FixedBondSpec, clean_price: float, call_index: int, *, as_of: date
) -> float:
    """The YTM solve run against one call: the bond truncated at the call
    date with redemption at the call price."""
    call = spec.calls[call_index]
    truncated = spec.model_copy(
        update={"maturity": call.start, "face": spec.face, "calls": (), "puts": ()}
    )
    # Redemption at call price: scale the redemption by price/100 via yield
    # solve on a bond whose final flow is the call price.
    with _ql.evaluation_date(as_of):
        cal = _ql.calendar(spec.calendar)
        schedule = ql.Schedule(
            _ql.to_ql_date(spec.issue_date),
            _ql.to_ql_date(call.start),
            ql.Period(_FREQ_TO_QL[spec.frequency]),
            cal,
            _ql.business_day(spec.business_day),
            _ql.business_day(spec.business_day),
            ql.DateGeneration.Backward,
            False,
        )
        bond = ql.FixedRateBond(
            truncated.settlement_days,
            truncated.face,
            schedule,
            [truncated.coupon],
            _ql.day_counter(truncated.day_count),
            _ql.business_day(truncated.business_day),
            call.price,  # redemption at the call price
        )
        return float(
            bond.bondYield(
                ql.BondPrice(clean_price, ql.BondPrice.Clean),
                _ql.day_counter(spec.day_count),
                ql.Compounded,
                _FREQ_TO_QL[spec.frequency],
                bond.settlementDate(),
                1e-12,
                300,
            )
        )


@model(
    model_id="bonds.yield_to_worst",
    version="1.0",
    spec_section="§10.1",
    summary="Minimum yield across maturity and every call date",
)
def yield_to_worst(spec: FixedBondSpec, clean_price: float, *, as_of: date) -> float:
    candidates = [yield_from_price.__wrapped__(spec, clean_price, as_of=as_of)]  # type: ignore[attr-defined]
    for i, call in enumerate(spec.calls):
        # A call exercisable in the past is no longer a distinct scenario.
        if call.start > as_of:
            candidates.append(
                yield_to_call.__wrapped__(spec, clean_price, i, as_of=as_of)  # type: ignore[attr-defined]
            )
    return float(min(candidates))


def _callable_bond_and_curve(
    spec: FixedBondSpec, curve: Curve, as_of: date
) -> tuple[ql.CallableFixedRateBond, ql.YieldTermStructureHandle]:
    """Build the QuantLib callable bond and a QL term structure sampled from
    our curve. Must be called inside evaluation_date(). The handle must stay
    referenced while the bond lives (CLAUDE.md §5 Trap 2) — callers keep both."""
    cal = _ql.calendar(spec.calendar)
    schedule = ql.Schedule(
        _ql.to_ql_date(spec.issue_date),
        _ql.to_ql_date(spec.maturity),
        ql.Period(_FREQ_TO_QL[spec.frequency]),
        cal,
        _ql.business_day(spec.business_day),
        _ql.business_day(spec.business_day),
        ql.DateGeneration.Backward,
        False,
    )
    call_sched = ql.CallabilitySchedule()
    for call in spec.calls:
        call_sched.append(
            ql.Callability(
                ql.BondPrice(call.price, ql.BondPrice.Clean),
                ql.Callability.Call,
                _ql.to_ql_date(call.start),
            )
        )
    for put in spec.puts:
        call_sched.append(
            ql.Callability(
                ql.BondPrice(put.price, ql.BondPrice.Clean),
                ql.Callability.Put,
                _ql.to_ql_date(put.start),
            )
        )
    # Sample our curve onto a QL zero curve at monthly resolution out to
    # maturity — dense enough that the QL interpolation between samples is
    # negligible against the lattice discretisation.
    dc = _ql.day_counter(spec.day_count)
    start = _ql.to_ql_date(as_of)
    months = max(dc.yearFraction(start, _ql.to_ql_date(spec.maturity)), 1.0) * 12.0
    dates = [start]
    zeros = [curve.zero(1.0 / 365.0)]
    for m in range(1, int(months) + 13):
        d = ql.TARGET().advance(start, ql.Period(m, ql.Months), ql.Unadjusted)
        t = dc.yearFraction(start, d)
        dates.append(d)
        zeros.append(curve.zero(t))
    ts = ql.ZeroCurve(dates, zeros, dc)
    ts.enableExtrapolation()
    handle = ql.YieldTermStructureHandle(ts)
    bond = ql.CallableFixedRateBond(
        spec.settlement_days,
        spec.face,
        schedule,
        [spec.coupon],
        _ql.day_counter(spec.day_count),
        _ql.business_day(spec.business_day),
        spec.face,
        _ql.to_ql_date(spec.issue_date),
        call_sched,
    )
    return bond, handle


@model(
    model_id="bonds.oas_hull_white_lattice",
    version="1.0",
    spec_section="§10.2",
    summary="OAS on a Hull-White trinomial lattice; vol/mean-reversion are "
    "explicit user parameters (ADR-0003), not market-calibrated",
)
def oas(
    spec: FixedBondSpec,
    clean_price: float,
    curve: Curve,
    *,
    as_of: date,
    volatility: float,
    mean_reversion: float = 0.03,
) -> float:
    with _ql.evaluation_date(as_of):
        bond, handle = _callable_bond_and_curve(spec, curve, as_of)
        model_hw = ql.HullWhite(handle, mean_reversion, volatility)
        engine = ql.TreeCallableFixedRateBondEngine(model_hw, _LATTICE_STEPS, handle)
        bond.setPricingEngine(engine)
        return float(
            bond.OAS(
                clean_price,
                handle,
                _ql.day_counter(spec.day_count),
                ql.Compounded,
                _FREQ_TO_QL[spec.frequency],
            )
        )


@model(
    model_id="bonds.lattice_price",
    version="1.0",
    spec_section="§10.2",
    summary="Callable bond clean price from the Hull-White lattice at a given OAS",
)
def lattice_price(
    spec: FixedBondSpec,
    oas_spread: float,
    curve: Curve,
    *,
    as_of: date,
    volatility: float,
    mean_reversion: float = 0.03,
) -> float:
    with _ql.evaluation_date(as_of):
        bond, handle = _callable_bond_and_curve(spec, curve, as_of)
        model_hw = ql.HullWhite(handle, mean_reversion, volatility)
        engine = ql.TreeCallableFixedRateBondEngine(model_hw, _LATTICE_STEPS, handle)
        bond.setPricingEngine(engine)
        return float(
            bond.cleanPriceOAS(
                oas_spread,
                handle,
                _ql.day_counter(spec.day_count),
                ql.Compounded,
                _FREQ_TO_QL[spec.frequency],
            )
        )


@model(
    model_id="bonds.effective_duration",
    version="1.0",
    spec_section="§10.1",
    summary="Effective duration: whole-curve bump, full lattice revaluation",
)
def effective_duration(
    spec: FixedBondSpec,
    clean_price: float,
    curve: Curve,
    *,
    as_of: date,
    volatility: float,
    mean_reversion: float = 0.03,
    bump: float = 0.0025,
) -> float:
    with _ql.evaluation_date(as_of):
        bond, handle = _callable_bond_and_curve(spec, curve, as_of)
        model_hw = ql.HullWhite(handle, mean_reversion, volatility)
        engine = ql.TreeCallableFixedRateBondEngine(model_hw, _LATTICE_STEPS, handle)
        bond.setPricingEngine(engine)
        implied_oas = float(
            bond.OAS(
                clean_price,
                handle,
                _ql.day_counter(spec.day_count),
                ql.Compounded,
                _FREQ_TO_QL[spec.frequency],
            )
        )
        return float(
            bond.effectiveDuration(
                implied_oas,
                handle,
                _ql.day_counter(spec.day_count),
                ql.Compounded,
                _FREQ_TO_QL[spec.frequency],
                bump,
            )
        )
