"""The `SWPM` curve environment, assembled from stored swap prints (§12.1).

Turns `swap:*` facts — the DTCC SDR adapter's output — into a built
:class:`CurveSet`: `EUR-ESTR-OIS` discounting, `EUR-EURIBOR-6M` forecasting.
That pairing is not a preference. An overnight index compounds daily, so an
OIS curve discounts but cannot project a discrete index, and the pricer
refuses it as a forecast curve. EURIBOR is a term index and can.

**Two refusals, both of which would otherwise produce a plausible screen:**

1. *One date, or nothing.* Every node must come from the same trading day.
   A curve whose front end is today's and whose long end is last Tuesday's
   is smooth, sensible-looking, and wrong — the same failure `ICVS` avoids
   by carrying an observation date per tenor.
2. *Common tenors only.* The forecast curve is solved against the discount
   curve, so a EURIBOR node beyond the last ESTR node would be discounted
   by extrapolation. Extrapolated discounting does not announce itself; it
   just makes the long end quietly wrong.

Lives in `tapi` rather than `analytics` because reading the store is TAPI's
job and screens must not reach past it (I7). The curve *construction* is
still `analytics`; this only supplies its inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from treble.analytics._ql import DayCount, Market
from treble.analytics.curves.bootstrap import CurveBuildError
from treble.analytics.curves.config import CurveConfig, InstrumentKind, InstrumentSpec
from treble.analytics.curves.multicurve import CurveSet, CurveSpec
from treble.core.identifiers import TUID
from treble.store.duck import DuckStore

#: The euro pair. Named here rather than discovered from the store so the
#: screen shows one defined environment: "whatever curves happen to be
#: ingested" would change what a valuation means between two runs.
DISCOUNT_CURVE = "EUR-ESTR-OIS"
FORECAST_CURVE = "EUR-EURIBOR-6M"
INDEX_TENOR = "6M"

#: The second forecast tenor. Built when the day carries it, and omitted
#: when it does not — a tenor basis needs two real curves, and inventing the
#: shorter one by interpolating the longer would make the basis a function of
#: the interpolator rather than of the market.
SHORT_FORECAST_CURVE = "EUR-EURIBOR-3M"
SHORT_INDEX_TENOR = "3M"
CALENDAR = Market.TARGET

#: A curve needs enough nodes to be a curve. Below this the screen says so
#: rather than drawing a line through three points.
MIN_NODES = 5


@dataclass(frozen=True)
class SwapMarket:
    """A built curve environment and the day it describes."""

    curves: CurveSet
    report_date: date
    tenors: tuple[str, ...]
    discount_rates: dict[str, float]
    forecast_rates: dict[str, float]
    #: The 3M quotes, when the day carried enough of them. Empty rather than
    #: absent so a caller reads "no short curve today" from a length instead
    #: of from a None it has to remember to check.
    short_rates: dict[str, float] = field(default_factory=dict)

    @property
    def basis_bp(self) -> dict[str, float]:
        """Forecast minus discount, in basis points, per tenor."""
        return {t: (self.forecast_rates[t] - self.discount_rates[t]) * 1e4 for t in self.tenors}


class SwapMarketUnavailableError(RuntimeError):
    """No coherent curve environment can be built from what is stored.

    Raised rather than returning a partial market. `SWPM` showing a PV
    computed from half a curve is worse than `SWPM` saying it has no
    curve, because only one of those is visibly missing.
    """


def _tenor_years(tenor: str) -> int:
    return int(tenor.removesuffix("Y"))


def _curve_quotes(store: DuckStore, curve: str, *, as_of: datetime) -> dict[date, dict[str, float]]:
    """Par rates by trading day and tenor for one curve."""
    by_date: dict[date, dict[str, float]] = {}
    for subject in store.subjects_with_prefix(f"swap:{curve}:", as_of=as_of):
        tenor = str(subject).rsplit(":", 1)[-1]
        if not tenor.endswith("Y"):
            continue
        for fact in store.read(TUID(str(subject)), "PAR_RATE", as_of=as_of):
            value = fact.value
            if not isinstance(value, int | float) or isinstance(value, bool):
                continue
            # A rate with no trading day is not a curve point. Keying it
            # under `None` would build a phantom day's curve that looks
            # like every other day's.
            if fact.effective_to is None:
                continue
            by_date.setdefault(fact.effective_to, {})[tenor] = float(value)
    return by_date


def build_swap_market(
    store: DuckStore, *, as_of: datetime, report_date: date | None = None
) -> SwapMarket:
    """A curve environment for one trading day.

    `report_date` defaults to the most recent day on which both curves build.
    Naming a day is what lets a caller value a print against the curve of the
    day it printed on: a swaption from the 13th priced off the 31st's curve
    has an eighteen-day-stale forward, which misplaces its moneyness and
    inflates its implied volatility — measured, not hypothetical.
    """
    discount_days = _curve_quotes(store, DISCOUNT_CURVE, as_of=as_of)
    forecast_days = _curve_quotes(store, FORECAST_CURVE, as_of=as_of)
    short_days = _curve_quotes(store, SHORT_FORECAST_CURVE, as_of=as_of)
    shared_days = sorted(set(discount_days) & set(forecast_days), reverse=True)
    if not shared_days:
        raise SwapMarketUnavailableError(
            f"no trading day carries both {DISCOUNT_CURVE} and {FORECAST_CURVE}; "
            "run `treble populate` to ingest DTCC SDR prints"
        )
    if report_date is not None:
        if report_date not in shared_days:
            raise SwapMarketUnavailableError(
                f"{report_date} carries no curve pair. Falling back to another day would "
                f"value that day's trades against a different day's market. Available: "
                f"{', '.join(str(d) for d in shared_days[:5])}"
            )
        shared_days = [report_date]

    failures: list[str] = []
    for report_date in shared_days:
        discount_rates = discount_days[report_date]
        forecast_rates = forecast_days[report_date]
        # Common tenors only: a forecast node beyond the discount curve's
        # last node would be discounted by extrapolation.
        tenors = sorted(set(discount_rates) & set(forecast_rates), key=_tenor_years)
        if len(tenors) < MIN_NODES:
            failures.append(f"{report_date}: only {len(tenors)} shared tenors")
            continue
        short_rates = short_days.get(report_date)
        short_tenors = (
            sorted(set(tenors) & set(short_rates), key=_tenor_years) if short_rates else []
        )
        try:
            curves = _build(
                report_date,
                tenors,
                discount_rates,
                forecast_rates,
                short_rates if len(short_tenors) >= MIN_NODES else None,
                short_tenors,
            )
        except CurveBuildError as error:  # a bad day is skipped, not fatal
            failures.append(f"{report_date}: {error}")
            continue
        return SwapMarket(
            curves=curves,
            report_date=report_date,
            tenors=tuple(tenors),
            discount_rates={t: discount_rates[t] for t in tenors},
            forecast_rates={t: forecast_rates[t] for t in tenors},
            short_rates=(
                {t: short_rates[t] for t in short_tenors}
                if short_rates and len(short_tenors) >= MIN_NODES
                else {}
            ),
        )

    raise SwapMarketUnavailableError(
        "no stored trading day produced a curve pair that reprices its inputs: "
        + "; ".join(failures[:3])
    )


def _build(
    report_date: date,
    tenors: list[str],
    discount_rates: dict[str, float],
    forecast_rates: dict[str, float],
    short_rates: dict[str, float] | None = None,
    short_tenors: list[str] | None = None,
) -> CurveSet:
    discount_config = CurveConfig(
        name=DISCOUNT_CURVE,
        currency="EUR",
        calendar=CALENDAR,
        instruments=tuple(InstrumentSpec(kind=InstrumentKind.OIS, tenor=t) for t in tenors),
    )
    forecast_config = CurveConfig(
        name=FORECAST_CURVE,
        currency="EUR",
        calendar=CALENDAR,
        index_tenor=INDEX_TENOR,
        discount_basis=DISCOUNT_CURVE,
        # EUR market convention: annual 30/360 fixed against ACT/360
        # floating. Stated on the curve *and* on the trade, because the
        # cross-check only means anything if both describe one instrument
        # (ADR-0006).
        swap_fixed_frequency=1,
        fixed_leg_day_count=DayCount.THIRTY_360,
        float_leg_day_count=DayCount.ACT_360,
        instruments=tuple(InstrumentSpec(kind=InstrumentKind.SWAP, tenor=t) for t in tenors),
    )
    return CurveSet(
        report_date,
        [
            CurveSpec(
                forecast_config, {(InstrumentKind.SWAP, t): forecast_rates[t] for t in tenors}
            ),
            CurveSpec(
                discount_config, {(InstrumentKind.OIS, t): discount_rates[t] for t in tenors}
            ),
            *_short_specs(short_rates, short_tenors),
        ],
    )


def _short_specs(
    short_rates: dict[str, float] | None, short_tenors: list[str] | None
) -> list[CurveSpec]:
    """The 3M forecast curve, when the day carries enough of it.

    Returned as a list so its absence is structural rather than a `None` a
    caller has to remember to handle: a day with no 3M prints simply yields a
    CurveSet without that curve, and asking for it raises `UnknownCurveError`
    naming what is present. A 3M curve interpolated from 6M quotes would make
    the tenor basis a property of the interpolator.
    """
    if not short_rates or not short_tenors:
        return []
    config = CurveConfig(
        name=SHORT_FORECAST_CURVE,
        currency="EUR",
        calendar=CALENDAR,
        index_tenor=SHORT_INDEX_TENOR,
        discount_basis=DISCOUNT_CURVE,
        swap_fixed_frequency=1,
        fixed_leg_day_count=DayCount.THIRTY_360,
        float_leg_day_count=DayCount.ACT_360,
        instruments=tuple(InstrumentSpec(kind=InstrumentKind.SWAP, tenor=t) for t in short_tenors),
    )
    return [CurveSpec(config, {(InstrumentKind.SWAP, t): short_rates[t] for t in short_tenors})]


__all__ = [
    "CALENDAR",
    "DISCOUNT_CURVE",
    "FORECAST_CURVE",
    "MIN_NODES",
    "SHORT_FORECAST_CURVE",
    "SwapMarket",
    "SwapMarketUnavailableError",
    "build_swap_market",
]
