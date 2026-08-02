"""The multi-curve framework (spec §11.1, Phase 2 `SWPM`).

    Discounting curve ≠ forecasting curve.

That is the post-2008 architecture in one line, and everything here exists
to make the inequality structural rather than remembered.

Before 2008 one curve did both jobs. A swap's floating leg was valued by
projecting forwards off the same curve used to discount them, and the
arithmetic collapsed: the whole floating leg telescoped to
``D(t_0) - D(t_n)``, independent of the forwards. That identity is *false*
in a multi-curve world, and it is the single most dangerous thing about
this subject — because code that still relies on it produces numbers that
look completely ordinary. There is no exception, no NaN, no obviously silly
figure. A long-dated swap is simply mispriced by the basis, every day,
quietly.

So this module refuses the shortcut in three ways:

1. :func:`build_forecast_curve` **requires** an exogenous discount curve.
   There is no default and no fallback to self-discounting. A forecast
   curve whose ``discount_basis`` is ``"self"`` is rejected, because that
   string means single-curve and the two must not be interchangeable.
2. Forwards come from the forecast curve's pseudo-discount factors and
   *only* from there; discount factors come from the discount curve and
   only from there. The residual is written so that no term can take its
   discounting from the curve being solved — the discount factors are
   computed once, before the solve, from a curve the solver cannot reach.
3. Times never cross a curve boundary. Two curves may use different day
   counts, and a year fraction computed under ACT/365F handed to an
   ACT/360 curve is wrong by about 1.4%.

:class:`CurveSet` holds the curves that were built together *and the quotes
that built them*. Retaining the quotes is what lets risk be computed by
rebuilding from a bumped market rather than by shifting the solved zeros —
the difference matters, because shifting zeros perturbs a curve into one
that no longer reprices any traded instrument, and then reports the
sensitivity of that fiction.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

import numpy as np
import QuantLib as ql
from scipy.optimize import root

from treble.analytics import _ql
from treble.analytics._ql import DayCount
from treble.analytics.curves.bootstrap import (
    REPRICE_TOLERANCE,
    Curve,
    CurveBuildError,
    build_curve,
)
from treble.analytics.curves.config import CurveConfig, InstrumentKind, InstrumentSpec
from treble.analytics.curves.interpolators import make_interpolator

#: Quotes are keyed by the instrument they price, never by position — a
#: positional list silently re-labels every quote when one instrument is
#: inserted into the middle of a curve definition.
QuoteMap = Mapping[tuple[InstrumentKind, str], float]

#: A discount-factor function of time, in the day count of whichever curve
#: it came from. Callers pass dates and convert per curve; this alias exists
#: only inside the solve, where the times are already in one curve's units.
Projector = Callable[[float], float]


class UnknownCurveError(KeyError):
    """A curve was referenced by name and is not in the set.

    Raised rather than resolved to a default. A missing discount curve that
    quietly became the forecast curve is exactly the single-curve mistake
    this module exists to prevent.
    """


def _year_fraction(day_count: DayCount, start: date, end: date) -> float:
    """Year fraction in a named convention, from QuantLib's day counters."""
    return float(
        _ql.day_counter(day_count).yearFraction(_ql.to_ql_date(start), _ql.to_ql_date(end))
    )


@dataclass(frozen=True)
class _Leg:
    """One leg of a curve input, reduced to what the residual needs.

    Discount factors are baked in rather than recomputed each iteration:
    the discount curve is exogenous and fixed for the whole solve, so a
    residual that recomputed them would be inviting the solver's candidate
    zeros to leak into the discounting.
    """

    accruals: tuple[float, ...]
    discount_factors: tuple[float, ...]
    #: Projection-curve times bracketing each floating period, in the
    #: projecting curve's own day count. Empty on a fixed leg.
    start_times: tuple[float, ...] = ()
    end_times: tuple[float, ...] = ()


@dataclass(frozen=True)
class _ForecastInput:
    spec: InstrumentSpec
    quote: float
    maturity_time: float
    fixed: _Leg | None = None
    floating: _Leg | None = None
    #: The reference leg of a tenor basis swap, already valued off another
    #: forecast curve — fixed for the whole solve.
    reference_pv: float = 0.0
    deposit_start_time: float = 0.0
    deposit_end_time: float = 0.0
    deposit_accrual: float = 0.0


def _schedule_dates(
    config: CurveConfig, as_of: date, tenor: str, frequency: int
) -> tuple[date, ...]:
    """Payment dates from spot to maturity at ``frequency`` per year.

    Built by QuantLib's ``Schedule`` with a real calendar and roll
    convention. Hand-rolled month arithmetic gets month-end and holiday
    rolls wrong in ways that move a coupon by a day and a PV by an amount
    nobody traces (CLAUDE.md §11).
    """
    cal = _ql.calendar(config.calendar)
    start = _ql.to_ql_date(as_of)
    spot = cal.advance(start, ql.Period(config.settlement_days, ql.Days))
    maturity = cal.advance(spot, ql.Period(tenor))
    schedule = ql.Schedule(
        spot,
        maturity,
        ql.Period(12 // frequency, ql.Months),
        cal,
        _ql.business_day(_ql.BusinessDay.MODIFIED_FOLLOWING),
        _ql.business_day(_ql.BusinessDay.MODIFIED_FOLLOWING),
        ql.DateGeneration.Backward,
        False,
    )
    return tuple(_ql.from_ql_date(d) for d in schedule)


def _make_leg(
    dates: Sequence[date],
    *,
    accrual_day_count: DayCount,
    discount: Curve,
    projection_day_count: DayCount | None,
    as_of: date,
) -> _Leg:
    """Accruals, discount factors, and projection times for one leg.

    Three day counts meet here and none of them may be assumed equal: the
    leg accrues in its own convention, the discount factors come from the
    discount curve's, and the projection times are in the forecasting
    curve's. Passing them separately is what stops one standing in for
    another.
    """
    accruals = tuple(_year_fraction(accrual_day_count, a, b) for a, b in pairwise(dates))
    discount_factors = tuple(discount.discount_at(d) for d in dates[1:])
    if projection_day_count is None:
        return _Leg(accruals=accruals, discount_factors=discount_factors)
    return _Leg(
        accruals=accruals,
        discount_factors=discount_factors,
        start_times=tuple(_year_fraction(projection_day_count, as_of, d) for d in dates[:-1]),
        end_times=tuple(_year_fraction(projection_day_count, as_of, d) for d in dates[1:]),
    )


def _floating_pv(leg: _Leg, spread: float, project: Projector) -> float:
    """PV of a floating leg: forwards from one curve, discounting another.

    Written as ``(P(s)/P(e) - 1)`` rather than ``F * τ`` because the accrual
    factor cancels exactly against the one inside the forward. Writing it
    out would introduce a day-count choice that has no effect on the answer
    but every appearance of having one.
    """
    total = 0.0
    for start, end, accrual, discount_factor in zip(
        leg.start_times, leg.end_times, leg.accruals, leg.discount_factors, strict=True
    ):
        growth = project(start) / project(end) - 1.0
        total += (growth + spread * accrual) * discount_factor
    return total


def _annuity(leg: _Leg) -> float:
    return sum(a * df for a, df in zip(leg.accruals, leg.discount_factors, strict=True))


def _forecast_inputs(
    config: CurveConfig,
    quotes: QuoteMap,
    *,
    discount: Curve,
    as_of: date,
    basis_reference: Curve | None,
) -> list[_ForecastInput]:
    """Reduce every curve instrument to residual inputs, or refuse."""
    index_tenor = config.index_tenor
    if index_tenor is None:  # pragma: no cover - guarded by the caller
        raise CurveBuildError(f"{config.name!r} has no index_tenor")
    float_frequency = config.index_frequency
    day_count = config.day_count
    inputs: list[_ForecastInput] = []

    for spec in config.instruments:
        key = (spec.kind, spec.tenor)
        if key not in quotes:
            raise CurveBuildError(f"no quote supplied for {spec.kind.value} {spec.tenor}")
        quote = quotes[key]

        if spec.kind == InstrumentKind.OIS:
            raise CurveBuildError(
                f"{config.name!r} lists an OIS at {spec.tenor}: an overnight-index "
                "swap says nothing about this curve's index forwards, so it would add "
                "an equation about the discount curve to a solve whose unknowns are "
                "forecast zeros"
            )

        if spec.kind == InstrumentKind.DEPOSIT:
            dates = _schedule_dates(config, as_of, spec.tenor, float_frequency)
            spot, maturity = dates[0], dates[-1]
            inputs.append(
                _ForecastInput(
                    spec=spec,
                    quote=quote,
                    maturity_time=_year_fraction(day_count, as_of, maturity),
                    deposit_start_time=_year_fraction(day_count, as_of, spot),
                    deposit_end_time=_year_fraction(day_count, as_of, maturity),
                    deposit_accrual=_year_fraction(config.float_leg_convention, spot, maturity),
                )
            )
            continue

        float_dates = _schedule_dates(config, as_of, spec.tenor, float_frequency)
        floating = _make_leg(
            float_dates,
            accrual_day_count=config.float_leg_convention,
            discount=discount,
            projection_day_count=day_count,
            as_of=as_of,
        )
        maturity_time = _year_fraction(day_count, as_of, float_dates[-1])

        if spec.kind == InstrumentKind.SWAP:
            inputs.append(
                _ForecastInput(
                    spec=spec,
                    quote=quote,
                    maturity_time=maturity_time,
                    fixed=_make_leg(
                        _schedule_dates(config, as_of, spec.tenor, config.swap_fixed_frequency),
                        accrual_day_count=config.fixed_leg_convention,
                        discount=discount,
                        projection_day_count=None,
                        as_of=as_of,
                    ),
                    floating=floating,
                )
            )
            continue

        # Tenor basis: this curve's leg carries the quoted spread; the
        # reference leg is flat and projects off an already-built curve.
        if basis_reference is None:
            raise CurveBuildError(
                f"{config.name!r} lists a basis swap at {spec.tenor} but no reference "
                "curve was supplied; a basis needs the other index"
            )
        reference_tenor = basis_reference.config.index_tenor
        if reference_tenor is None:
            raise CurveBuildError(
                f"basis reference {basis_reference.config.name!r} forecasts no index, "
                "so there is no second index for the spread to be a basis between"
            )
        if reference_tenor == index_tenor:
            raise CurveBuildError(
                f"basis reference {basis_reference.config.name!r} forecasts the same "
                f"{index_tenor} index as {config.name!r}: the two legs would be "
                "identical and the quote would carry no information"
            )
        reference_leg = _make_leg(
            _schedule_dates(
                basis_reference.config,
                as_of,
                spec.tenor,
                basis_reference.config.index_frequency,
            ),
            accrual_day_count=basis_reference.config.float_leg_convention,
            discount=discount,
            projection_day_count=basis_reference.config.day_count,
            as_of=as_of,
        )
        inputs.append(
            _ForecastInput(
                spec=spec,
                quote=quote,
                maturity_time=maturity_time,
                floating=floating,
                reference_pv=_floating_pv(reference_leg, 0.0, basis_reference.discount),
            )
        )
    return inputs


def build_forecast_curve(
    config: CurveConfig,
    quotes: QuoteMap,
    *,
    discount: Curve,
    as_of: date,
    basis_reference: Curve | None = None,
) -> Curve:
    """Bootstrap an index forecast curve against an exogenous discount curve.

    The unknowns are this curve's node zeros; the discount curve is an
    input, not an output. Every input instrument must reprice to its quote
    within :data:`REPRICE_TOLERANCE`, and a curve that does not is an error
    rather than a warning — the same gate the single-curve bootstrap uses.
    """
    if config.index_tenor is None:
        raise CurveBuildError(
            f"{config.name!r} has no index_tenor: a forecast curve projects a specific "
            "index, and without naming it the float leg frequency would be a guess "
            "baked into every forward"
        )
    if config.discount_basis == "self":
        raise CurveBuildError(
            f"{config.name!r} has discount_basis='self', which means single-curve. A "
            "forecast curve must name the curve that discounts it; allowing 'self' "
            "here would let a self-discounting curve be built by the multi-curve path "
            "and be indistinguishable from a real one"
        )

    with _ql.evaluation_date(as_of):
        inputs = _forecast_inputs(
            config,
            quotes,
            discount=discount,
            as_of=as_of,
            basis_reference=basis_reference,
        )

    inputs.sort(key=lambda i: i.maturity_time)
    times = tuple(i.maturity_time for i in inputs)
    if len(set(times)) != len(times):
        raise CurveBuildError("two instruments share a maturity node")

    def residuals(zeros: np.ndarray) -> np.ndarray:
        interp = make_interpolator(config.interpolation, times, tuple(float(z) for z in zeros))
        out = np.empty(len(inputs))
        for i, inp in enumerate(inputs):
            if inp.spec.kind == InstrumentKind.DEPOSIT:
                growth = (
                    interp.discount(inp.deposit_start_time) / interp.discount(inp.deposit_end_time)
                    - 1.0
                )
                out[i] = growth / inp.deposit_accrual - inp.quote
            elif inp.floating is None:  # pragma: no cover - constructed above
                raise CurveBuildError(f"{inp.spec.kind.value} built without a floating leg")
            elif inp.spec.kind == InstrumentKind.SWAP:
                if inp.fixed is None:  # pragma: no cover - constructed above
                    raise CurveBuildError("swap built without a fixed leg")
                float_pv = _floating_pv(inp.floating, 0.0, interp.discount)
                out[i] = float_pv / _annuity(inp.fixed) - inp.quote
            else:  # BASIS
                this_pv = _floating_pv(inp.floating, inp.quote, interp.discount)
                out[i] = this_pv - inp.reference_pv
        return out

    guess = np.array([inp.quote for inp in inputs])
    solution = root(residuals, guess, method="hybr")
    # As in the single-curve bootstrap: the repricing property is the gate,
    # not the solver's own verdict.
    worst = float(np.max(np.abs(residuals(solution.x))))
    if worst > REPRICE_TOLERANCE:
        raise CurveBuildError(
            f"{config.name!r} does not reprice its inputs: max residual {worst:.3e}"
            f" (solver: {solution.message})"
        )
    return Curve(config, as_of, times, tuple(float(z) for z in solution.x))


def build_csa_discount_curve(
    config: CurveConfig,
    *,
    base: Curve,
    basis_spreads: Mapping[str, float],
    as_of: date,
) -> Curve:
    """The curve that discounts cash flows collateralised in another currency.

    Posting collateral in a currency other than the trade's means the
    collateral is remunerated at *that* currency's overnight rate, and the
    difference is carried by the cross-currency basis. The discount curve is
    therefore the domestic overnight curve plus the basis — which is why
    changing the CSA currency on a long-dated swap moves its PV materially
    (spec §11.1) rather than cosmetically.

    **What this is.** The basis is applied as a term structure of continuous
    zero spreads at the quoted tenors, interpolated by the curve's own
    method. That is the standard market construction of a CSA discount
    curve and it is exact given the spreads.

    **What it is not.** It does not *solve* the spreads from cross-currency
    basis swaps, which additionally needs FX forwards and the foreign
    curve. Those spreads are an input here rather than an output, and the
    distinction is stated because an approximation reported as a solve is
    the failure this codebase keeps finding in itself.
    """
    if not basis_spreads:
        raise CurveBuildError(
            f"{config.name!r} is a CSA discount curve with no basis spreads; a foreign "
            "CSA with a zero basis is a claim about the market, so it must be quoted "
            "rather than arrived at by supplying nothing"
        )
    if config.index_tenor is not None:
        raise CurveBuildError(
            f"{config.name!r} is a discount curve but names index_tenor "
            f"{config.index_tenor!r}: discounting projects no index, and a curve that "
            "claimed to do both would be used for both"
        )
    if config.discount_basis == "self":
        raise CurveBuildError(
            f"{config.name!r} has discount_basis='self' but is built from basis "
            "spreads; it must name the overnight curve those spreads adjust"
        )

    with _ql.evaluation_date(as_of):
        cal = _ql.calendar(config.calendar)
        start = _ql.to_ql_date(as_of)
        nodes: list[tuple[float, float]] = []
        for tenor, spread in basis_spreads.items():
            maturity = _ql.from_ql_date(cal.advance(start, ql.Period(tenor)))
            nodes.append(
                (
                    _year_fraction(config.day_count, as_of, maturity),
                    base.zero_at(maturity) + spread,
                )
            )
    nodes.sort()
    times = tuple(t for t, _ in nodes)
    if len(set(times)) != len(times):
        raise CurveBuildError("two basis spreads share a maturity node")
    return Curve(config, as_of, times, tuple(z for _, z in nodes))


@dataclass(frozen=True)
class CurveSpec:
    """A curve definition plus the market that built it.

    The quotes travel with the config because risk is computed by
    rebuilding from a bumped market. A built curve alone cannot be
    re-solved, and the tempting alternative — shifting the solved zeros —
    reports the sensitivity of a curve that reprices nothing.
    """

    config: CurveConfig
    quotes: QuoteMap
    #: Name of the curve supplying the reference leg of any basis swaps.
    basis_reference: str | None = None
    #: Cross-currency basis spreads by tenor. Present makes this a CSA
    #: discount curve built off ``config.discount_basis`` rather than a
    #: forecast curve — see :func:`build_csa_discount_curve`.
    basis_spreads: Mapping[str, float] | None = None

    @property
    def dependencies(self) -> tuple[str, ...]:
        deps = []
        if self.config.discount_basis != "self":
            deps.append(self.config.discount_basis)
        if self.basis_reference is not None:
            deps.append(self.basis_reference)
        return tuple(deps)


class CurveSet:
    """Curves built together, in dependency order, with their quotes.

    Curves are keyed by ``config.name`` rather than by a caller-supplied
    label. Two names for one curve is a state in which the discount curve a
    result claims to have used and the one it did use can differ, and
    nothing downstream could tell.
    """

    def __init__(self, as_of: date, specs: Sequence[CurveSpec]) -> None:
        self.as_of = as_of
        self._specs: dict[str, CurveSpec] = {}
        for spec in specs:
            name = spec.config.name
            if name in self._specs:
                raise CurveBuildError(f"two curve definitions named {name!r}")
            self._specs[name] = spec
        self._curves: dict[str, Curve] = {}
        for name in self._build_order():
            self._curves[name] = self._build(self._specs[name])

    def _build_order(self) -> list[str]:
        """Topological order, refusing cycles.

        A cycle is a curve set that cannot be built at all; detecting it
        here beats a recursion limit, which reads as a bug in the solver
        rather than as a contradiction in the configuration.
        """
        order: list[str] = []
        state: dict[str, str] = {}

        def visit(name: str, trail: tuple[str, ...]) -> None:
            if state.get(name) == "done":
                return
            if state.get(name) == "visiting":
                raise CurveBuildError("circular curve dependency: " + " -> ".join([*trail, name]))
            if name not in self._specs:
                raise UnknownCurveError(
                    f"{name!r} is referenced as a dependency but is not in the set"
                )
            state[name] = "visiting"
            for dep in self._specs[name].dependencies:
                visit(dep, (*trail, name))
            state[name] = "done"
            order.append(name)

        for name in self._specs:
            visit(name, ())
        return order

    def _build(self, spec: CurveSpec) -> Curve:
        if spec.basis_spreads is not None:
            return build_csa_discount_curve(
                spec.config,
                base=self._curves[spec.config.discount_basis],
                basis_spreads=spec.basis_spreads,
                as_of=self.as_of,
            )
        if spec.config.discount_basis == "self":
            return build_curve(spec.config, dict(spec.quotes), as_of=self.as_of)
        return build_forecast_curve(
            spec.config,
            spec.quotes,
            discount=self._curves[spec.config.discount_basis],
            as_of=self.as_of,
            basis_reference=(self._curves[spec.basis_reference] if spec.basis_reference else None),
        )

    # -- access ---------------------------------------------------------

    def curve(self, name: str) -> Curve:
        try:
            return self._curves[name]
        except KeyError:
            raise UnknownCurveError(
                f"no curve named {name!r} in this set; have: " + ", ".join(sorted(self._curves))
            ) from None

    def __contains__(self, name: object) -> bool:
        return name in self._curves

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._curves))

    @property
    def content_hash(self) -> str:
        """One hash pinning the whole curve environment.

        A swap's PV depends on every curve that touched it, so the envelope
        records the environment as a unit; the individual config hashes stay
        on each curve for drill-down.
        """
        digest = hashlib.sha256()
        for name in sorted(self._curves):
            digest.update(name.encode())
            digest.update(self._curves[name].content_hash.encode())
        return digest.hexdigest()

    # -- risk -----------------------------------------------------------

    def bumped(
        self,
        basis_points: float,
        *,
        curve: str | None = None,
        instrument: tuple[InstrumentKind, str] | None = None,
    ) -> CurveSet:
        """A new set built from bumped market quotes.

        Rebuilt, not shifted. Bumping a quote and re-solving gives the
        sensitivity of a curve that still reprices every *other* instrument
        — which is what a hedge is quoted against. Shifting solved zeros
        gives the sensitivity of a curve that reprices nothing.

        ``curve`` and ``instrument`` narrow the bump; omitting both bumps
        every quote in the set, which is the parallel shift.
        """
        shift = basis_points * 1e-4
        if curve is not None and curve not in self._specs:
            raise UnknownCurveError(f"no curve named {curve!r} in this set")
        if instrument is not None and not self._has_instrument(curve, instrument):
            # A bucket matching nothing would rebuild an unchanged set and
            # report a DV01 of exactly zero — indistinguishable from a
            # genuinely insensitive bucket.
            raise UnknownCurveError(
                f"no instrument {instrument[0].value} {instrument[1]} to bump"
                + (f" on {curve!r}" if curve else " in this set")
            )
        rebuilt = [
            spec
            if (curve is not None and name != curve)
            else CurveSpec(
                config=spec.config,
                quotes={
                    key: (value + shift if instrument is None or key == instrument else value)
                    for key, value in spec.quotes.items()
                },
                basis_reference=spec.basis_reference,
            )
            for name, spec in self._specs.items()
        ]
        return CurveSet(self.as_of, rebuilt)

    def _has_instrument(self, curve: str | None, instrument: tuple[InstrumentKind, str]) -> bool:
        names = [curve] if curve is not None else list(self._specs)
        return any(instrument in self._specs[name].quotes for name in names)

    def buckets(self, curve: str) -> tuple[tuple[InstrumentKind, str], ...]:
        """The bumpable instruments of one curve, in curve order.

        Instruments the curve *defines* but has no quote for are excluded,
        because they cannot be bumped. A CSA discount curve built from
        basis spreads has no quotes of its own and therefore no buckets —
        correctly, since its sensitivity arrives through the overnight
        curve it is built from, which the dependency-ordered rebuild
        already propagates. Listing an unbumpable bucket would put a zero
        in the ladder that reads as "no exposure".
        """
        spec = self._specs.get(curve)
        if spec is None:
            raise UnknownCurveError(f"no curve named {curve!r} in this set")
        return tuple(
            (i.kind, i.tenor) for i in spec.config.instruments if (i.kind, i.tenor) in spec.quotes
        )


__all__ = [
    "CurveSet",
    "CurveSpec",
    "Projector",
    "QuoteMap",
    "UnknownCurveError",
    "build_csa_discount_curve",
    "build_forecast_curve",
]
