"""Straight-bond analytics (spec §10.1 YAS core).

Every public function is wrapped by @model (I3) and runs inside the locked
QuantLib evaluation-date context (CLAUDE.md §5). Curves arrive as
:class:`treble.analytics.curves.Curve`; their config hash lands in the
envelope automatically because ``Curve`` exposes ``content_hash``.

Yield conventions here are US street convention (compounded at the coupon
frequency on the bond's own day count). Further national conventions (§10.1)
are added as explicit options, never silently.
"""

from __future__ import annotations

from datetime import date

import QuantLib as ql

from treble.analytics import _ql
from treble.analytics.bonds.spec import FixedBondSpec, Frequency
from treble.analytics.curves.bootstrap import Curve
from treble.analytics.registry import model

_FREQ_TO_QL = {
    Frequency.ANNUAL: ql.Annual,
    Frequency.SEMIANNUAL: ql.Semiannual,
    Frequency.QUARTERLY: ql.Quarterly,
    Frequency.MONTHLY: ql.Monthly,
}


def _build_bond(spec: FixedBondSpec, as_of: date) -> tuple[ql.FixedRateBond, ql.Date]:
    """Construct the QuantLib bond. Must be called inside evaluation_date()."""
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
    bond = ql.FixedRateBond(
        spec.settlement_days,
        spec.face,
        schedule,
        [spec.coupon],
        _ql.day_counter(spec.day_count),
        # Payments follow the bond's own declared convention — QuantLib's
        # default (Following) would silently roll weekend payment dates even
        # for an UNADJUSTED bond (CLAUDE.md §11: silent calendar errors).
        _ql.business_day(spec.business_day),
    )
    return bond, bond.settlementDate()


@model(
    model_id="bonds.price_from_yield",
    version="1.0",
    spec_section="§10.1",
    summary="Clean price from street-convention yield",
)
def price_from_yield(spec: FixedBondSpec, yield_rate: float, *, as_of: date) -> float:
    with _ql.evaluation_date(as_of):
        bond, _settle = _build_bond(spec, as_of)
        rate = ql.InterestRate(
            yield_rate,
            _ql.day_counter(spec.day_count),
            ql.Compounded,
            _FREQ_TO_QL[spec.frequency],
        )
        return float(ql.BondFunctions.cleanPrice(bond, rate))


@model(
    model_id="bonds.yield_from_price",
    version="1.0",
    spec_section="§10.1",
    summary="Street-convention yield solved from clean price",
)
def yield_from_price(spec: FixedBondSpec, clean_price: float, *, as_of: date) -> float:
    with _ql.evaluation_date(as_of):
        bond, _settle = _build_bond(spec, as_of)
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
    model_id="bonds.accrued_interest",
    version="1.0",
    spec_section="§10.1",
    summary="Accrued to settlement on the bond's exact day count",
)
def accrued_interest(spec: FixedBondSpec, *, as_of: date) -> float:
    with _ql.evaluation_date(as_of):
        bond, _settle = _build_bond(spec, as_of)
        return float(bond.accruedAmount())


@model(
    model_id="bonds.cash_flows",
    version="1.0",
    spec_section="§10.1",
    summary="Remaining cash flow schedule (CSHF)",
)
def cash_flows(spec: FixedBondSpec, *, as_of: date) -> tuple[tuple[date, float], ...]:
    with _ql.evaluation_date(as_of):
        bond, settle = _build_bond(spec, as_of)
        flows: list[tuple[date, float]] = []
        for cf in bond.cashflows():
            if cf.date() > settle:
                flows.append((_ql.from_ql_date(cf.date()), float(cf.amount())))
        return tuple(flows)


@model(
    model_id="bonds.modified_duration",
    version="1.0",
    spec_section="§10.1",
    summary="Modified duration at the quoted yield",
)
def modified_duration(spec: FixedBondSpec, yield_rate: float, *, as_of: date) -> float:
    with _ql.evaluation_date(as_of):
        bond, _settle = _build_bond(spec, as_of)
        rate = ql.InterestRate(
            yield_rate,
            _ql.day_counter(spec.day_count),
            ql.Compounded,
            _FREQ_TO_QL[spec.frequency],
        )
        return float(ql.BondFunctions.duration(bond, rate, ql.Duration.Modified))


@model(
    model_id="bonds.macaulay_duration",
    version="1.0",
    spec_section="§10.1",
    summary="Macaulay duration at the quoted yield",
)
def macaulay_duration(spec: FixedBondSpec, yield_rate: float, *, as_of: date) -> float:
    with _ql.evaluation_date(as_of):
        bond, _settle = _build_bond(spec, as_of)
        rate = ql.InterestRate(
            yield_rate,
            _ql.day_counter(spec.day_count),
            ql.Compounded,
            _FREQ_TO_QL[spec.frequency],
        )
        return float(ql.BondFunctions.duration(bond, rate, ql.Duration.Macaulay))


@model(
    model_id="bonds.convexity",
    version="1.0",
    spec_section="§10.1",
    summary="Convexity at the quoted yield",
)
def convexity(spec: FixedBondSpec, yield_rate: float, *, as_of: date) -> float:
    with _ql.evaluation_date(as_of):
        bond, _settle = _build_bond(spec, as_of)
        rate = ql.InterestRate(
            yield_rate,
            _ql.day_counter(spec.day_count),
            ql.Compounded,
            _FREQ_TO_QL[spec.frequency],
        )
        return float(ql.BondFunctions.convexity(bond, rate))


@model(
    model_id="bonds.dv01",
    version="1.0",
    spec_section="§10.1",
    summary="Price value of a basis point (per 100 face)",
)
def dv01(spec: FixedBondSpec, yield_rate: float, *, as_of: date) -> float:
    up = price_from_yield.__wrapped__(spec, yield_rate + 0.0001, as_of=as_of)  # type: ignore[attr-defined]
    down = price_from_yield.__wrapped__(spec, yield_rate - 0.0001, as_of=as_of)  # type: ignore[attr-defined]
    return float(down - up) / 2.0


@model(
    model_id="bonds.z_spread",
    version="1.0",
    spec_section="§10.1",
    summary="Constant shift to the zero curve equating PV to price",
)
def z_spread(spec: FixedBondSpec, clean_price: float, curve: Curve, *, as_of: date) -> float:
    """Solved iteratively on continuously compounded shifts (spec §10.1)."""
    from scipy.optimize import brentq

    with _ql.evaluation_date(as_of):
        bond, settle = _build_bond(spec, as_of)
        dc = _ql.day_counter(spec.day_count)
        settle_time = dc.yearFraction(_ql.to_ql_date(as_of), settle)
        accrued = float(bond.accruedAmount())
        dirty_target = clean_price + accrued
        flows = [
            (dc.yearFraction(_ql.to_ql_date(as_of), cf.date()), float(cf.amount()))
            for cf in bond.cashflows()
            if cf.date() > settle
        ]

    def dirty_at(shift: float) -> float:
        import math

        pv = sum(amount * math.exp(-(curve.zero(t) + shift) * t) for t, amount in flows)
        # Forward-value to settlement so the comparison is at the settle date.
        return pv / math.exp(-(curve.zero(settle_time) + shift) * settle_time)

    return float(brentq(lambda s: dirty_at(s) - dirty_target, -0.05, 0.5, xtol=1e-12))


@model(
    model_id="bonds.g_spread",
    version="1.0",
    spec_section="§10.1",
    summary="Yield minus interpolated government curve at maturity",
)
def g_spread(spec: FixedBondSpec, clean_price: float, govt_curve: Curve, *, as_of: date) -> float:
    """Yield over the government curve, both on the bond's own basis.

    **The conversion is the whole correctness of this function.**
    `yield_from_price` returns a yield compounded at the bond's frequency;
    `Curve.zero` returns a *continuously* compounded rate, because that is
    what `exp(-zt)` discounting needs. Subtracting one from the other, as
    this did, is a units error — and a quiet one, because both numbers are
    rates near 4% and the difference is a plausible-looking spread.

    Measured 2026-08-11: a ten-year par Treasury priced at 100 on the curve
    it was built from reported a G-spread of **+5.38bp**, of which +5.32bp
    was the conversion. It is systematic and always the same sign, so it
    never reads as noise, and on a 100bp corporate spread it is a 5% error.

    The golden tests could not catch it: they compared against values
    computed the same mixed way. What catches it is the self-consistency
    check — a bond *on* the curve must show zero — which is now in the
    suite.
    """
    import math

    ytm = yield_from_price.__wrapped__(spec, clean_price, as_of=as_of)  # type: ignore[attr-defined]
    with _ql.evaluation_date(as_of):
        dc = _ql.day_counter(spec.day_count)
        t_maturity = dc.yearFraction(_ql.to_ql_date(as_of), _ql.to_ql_date(spec.maturity))
    # Continuous -> the bond's compounding frequency, so the subtraction is
    # between two rates quoted the same way. Market convention quotes a
    # G-spread on the bond's basis, which is why the curve moves to the
    # bond rather than the other way round.
    frequency = float(spec.frequency.value)
    continuous = govt_curve.zero(t_maturity)
    benchmark = frequency * (math.exp(continuous / frequency) - 1.0)
    return float(ytm - benchmark)
