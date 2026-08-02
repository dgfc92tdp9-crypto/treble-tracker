"""Curve bootstrapping (spec §11.1.5): solve node zeros so that every input
instrument reprices to market exactly.

The solve is *global* — spline and monotone convex interpolation are
non-local, so a later node moves discount factors before it; a sequential
bootstrap would leave residuals. Unknowns are the zero rates at instrument
maturity nodes; residuals are model-minus-market prices; scipy's hybrid
Powell solver drives them to machine zero. The repricing property (every
input to 1e-10) is asserted by the golden tests on every method — and
enforced here at construction, because a curve that does not reprice its
inputs is a defect, not a warning.

Dates and year fractions use the QuantLib calendars and day counters via
``analytics._ql`` — never hand-rolled day math (CLAUDE.md §11 failure modes).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import pairwise

import numpy as np
import QuantLib as ql
from scipy.optimize import root

from treble.analytics import _ql
from treble.analytics.curves.config import CurveConfig, InstrumentKind, InstrumentSpec
from treble.analytics.curves.interpolators import Interpolator, make_interpolator


class CurveBuildError(Exception):
    """The solver failed or the built curve does not reprice its inputs."""


REPRICE_TOLERANCE = 1e-10


@dataclass(frozen=True)
class _PricedInstrument:
    spec: InstrumentSpec
    quote: float
    maturity_time: float
    # Fixed-leg payment times and accrual fractions (empty for deposits).
    pay_times: tuple[float, ...]
    accruals: tuple[float, ...]
    deposit_accrual: float


class Curve:
    """A bootstrapped curve. Carries its CurveConfig — construction without
    one is impossible (I4) — and exposes the config's content hash so the
    @model decorator stamps it into every envelope computed from this curve.
    """

    def __init__(
        self,
        config: CurveConfig,
        as_of: date,
        node_times: tuple[float, ...],
        node_zeros: tuple[float, ...],
    ) -> None:
        if not isinstance(config, CurveConfig):
            raise TypeError("a Curve cannot exist without a CurveConfig (I4)")
        self.config = config
        self.as_of = as_of
        self.node_times = node_times
        self.node_zeros = node_zeros
        self._interp: Interpolator = make_interpolator(config.interpolation, node_times, node_zeros)
        self._day_counter = _ql.day_counter(config.day_count)

    @property
    def content_hash(self) -> str:
        return self.config.content_hash

    def zero(self, t: float) -> float:
        return self._interp.zero(t)

    def discount(self, t: float) -> float:
        return self._interp.discount(t)

    # -- date-based access ---------------------------------------------
    #
    # Multi-curve pricing reads a discount factor from one curve and a
    # forward from another, and the two curves need not share a day count.
    # A time computed under ACT/365F and handed to an ACT/360 curve is a
    # silently wrong discount factor — off by ~1.4%, which on a 30-year
    # swap is real money and looks entirely plausible. Times therefore
    # never cross a curve boundary: callers pass dates, and each curve
    # converts using its own day counter.

    def time_to(self, d: date) -> float:
        """Year fraction from ``as_of`` to ``d`` in *this curve's* day count."""
        return float(self._day_counter.yearFraction(_ql.to_ql_date(self.as_of), _ql.to_ql_date(d)))

    def discount_at(self, d: date) -> float:
        return self.discount(self.time_to(d))

    def zero_at(self, d: date) -> float:
        return self.zero(self.time_to(d))

    def forward(self, t1: float, t2: float) -> float:
        """Continuously compounded forward between two times."""
        if t2 <= t1:
            raise ValueError("t2 must exceed t1")
        import math

        return math.log(self.discount(t1) / self.discount(t2)) / (t2 - t1)


def _instrument_times(
    config: CurveConfig, spec: InstrumentSpec, as_of: date
) -> tuple[float, tuple[float, ...], tuple[float, ...], float]:
    """Maturity time, fixed-leg pay times, accrual fractions, deposit accrual —
    from real calendars and day counters, not year/12 arithmetic."""
    cal = _ql.calendar(config.calendar)
    dc = _ql.day_counter(config.day_count)
    start = _ql.to_ql_date(as_of)
    spot = cal.advance(start, ql.Period(config.settlement_days, ql.Days))
    maturity = cal.advance(spot, ql.Period(spec.tenor))
    t_maturity = dc.yearFraction(start, maturity)

    if spec.kind == InstrumentKind.DEPOSIT:
        accrual = dc.yearFraction(spot, maturity)
        return t_maturity, (), (), accrual

    # OIS / swap: annual fixed leg from spot to maturity.
    schedule = ql.Schedule(
        spot,
        maturity,
        ql.Period(ql.Annual),
        cal,
        _ql.business_day(_ql.BusinessDay.MODIFIED_FOLLOWING),
        _ql.business_day(_ql.BusinessDay.MODIFIED_FOLLOWING),
        ql.DateGeneration.Forward,
        False,
    )
    dates = list(schedule)
    pay_times = tuple(dc.yearFraction(start, d) for d in dates[1:])
    accruals = tuple(dc.yearFraction(d1, d2) for d1, d2 in pairwise(dates))
    return t_maturity, pay_times, accruals, 0.0


def _residuals(
    zeros: np.ndarray,
    config: CurveConfig,
    times: tuple[float, ...],
    instruments: list[_PricedInstrument],
    t_spot: float,
) -> np.ndarray:
    interp = make_interpolator(config.interpolation, times, tuple(float(z) for z in zeros))
    df_spot = interp.discount(t_spot)
    out = np.empty(len(instruments))
    for i, inst in enumerate(instruments):
        if inst.spec.kind == InstrumentKind.DEPOSIT:
            implied = (df_spot / interp.discount(inst.maturity_time) - 1.0) / inst.deposit_accrual
            out[i] = implied - inst.quote
        else:
            annuity = sum(
                a * interp.discount(t) for a, t in zip(inst.accruals, inst.pay_times, strict=True)
            )
            par = (df_spot - interp.discount(inst.maturity_time)) / annuity
            out[i] = par - inst.quote
    return out


def build_curve(
    config: CurveConfig,
    quotes: dict[tuple[InstrumentKind, str], float],
    *,
    as_of: date,
) -> Curve:
    """Bootstrap the curve defined by ``config`` from market ``quotes``.

    Quotes are keyed by (kind, tenor) and must cover every instrument in the
    configuration — a missing quote is an error, never silently skipped.
    """
    with _ql.evaluation_date(as_of):
        instruments: list[_PricedInstrument] = []
        for spec in config.instruments:
            key = (spec.kind, spec.tenor)
            if key not in quotes:
                raise CurveBuildError(f"no quote supplied for {spec.kind.value} {spec.tenor}")
            t_mat, pay_times, accruals, dep_accrual = _instrument_times(config, spec, as_of)
            instruments.append(
                _PricedInstrument(
                    spec=spec,
                    quote=quotes[key],
                    maturity_time=t_mat,
                    pay_times=pay_times,
                    accruals=accruals,
                    deposit_accrual=dep_accrual,
                )
            )
        cal = _ql.calendar(config.calendar)
        dc = _ql.day_counter(config.day_count)
        start = _ql.to_ql_date(as_of)
        spot = cal.advance(start, ql.Period(config.settlement_days, ql.Days))
        t_spot = dc.yearFraction(start, spot)

    instruments.sort(key=lambda inst: inst.maturity_time)
    times = tuple(inst.maturity_time for inst in instruments)
    if len(set(times)) != len(times):
        raise CurveBuildError("two instruments share a maturity node")

    guess = np.array([inst.quote for inst in instruments])
    solution = root(
        _residuals,
        guess,
        args=(config, times, instruments, t_spot),
        method="hybr",
    )
    # The gate is the repricing property itself, not the solver's verdict:
    # hybr sometimes reports "xtol too small" after already driving residuals
    # to machine zero. Residuals above tolerance fail regardless of `success`.
    residuals = _residuals(solution.x, config, times, instruments, t_spot)
    worst = float(np.max(np.abs(residuals)))
    if worst > REPRICE_TOLERANCE:
        raise CurveBuildError(
            f"built curve does not reprice inputs: max residual {worst:.3e}"
            f" (solver: {solution.message})"
        )
    return Curve(config, as_of, times, tuple(float(z) for z in solution.x))
