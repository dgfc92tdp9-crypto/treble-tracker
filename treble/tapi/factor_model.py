"""The `PORT` risk environment, assembled from stored return series (§16.3).

Turns `factor:*` and `portfolio:*` `TOT_RETURN` facts — the Ken French
adapter's output — into a fitted model: a factor covariance, per-asset
exposures, and the specific risk from the same fit.

**Three refusals, each of which would otherwise produce a plausible screen:**

1. *Excess returns, not total returns.* The industry series are total
   returns and the factors are excess returns, so every asset's returns have
   the risk-free rate removed before regression. Skipping that puts the
   short rate into every beta — a small distortion at today's rates and a
   large one in the 1980s, and visible in neither case.
2. *Assets with a full history, or none.* An industry that did not exist for
   part of the window would otherwise be regressed on the days it does have,
   producing a beta estimated over a different period from its neighbours'.
   The screen would show one table whose rows describe different windows.
3. *A window long enough to estimate on.* Below `MIN_OBSERVATIONS` the
   covariance is refused upstream; here the reason arrives as text rather
   than as an exception a screen would render as an empty table.

Lives in `tapi` rather than `analytics` because reading the store is TAPI's
job and screens must not reach past it (I7). The estimation is still
`analytics`; this only supplies its inputs.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import date, datetime

from treble.analytics.risk.factors import (
    MIN_OBSERVATIONS,
    Exposures,
    FactorCovariance,
    ReturnPanel,
    estimate_exposures,
    factor_covariance,
)
from treble.core.identifiers import TUID
from treble.store.duck import DuckStore

#: The published factors this model is built on. Named rather than
#: discovered, so the screen shows one defined model: "whatever factors
#: happen to be ingested" would silently change what an exposure means
#: between two runs, and a beta is only interpretable against a stated set.
FACTORS: tuple[str, ...] = ("MKT_RF", "SMB", "HML", "RMW", "CMA", "MOM")

#: The risk-free series, used to convert total returns to excess returns.
#: Not a factor: regressing an asset on the risk-free rate as though it were
#: one would report a "beta to cash".
RISK_FREE = "RF"

#: How many trading days the model is fitted over. Five years is long enough
#: for a stable covariance over six factors and short enough to describe a
#: recognisable regime. Stated because the answer depends on it: the same
#: portfolio has a different risk number over a window containing a crisis.
WINDOW_DAYS = 1260

#: The subject namespaces. Factors and assets are deliberately separate, so
#: a factor cannot be regressed on itself.
FACTOR_NS = "factor"
ASSET_NS = "portfolio"


class FactorModelUnavailableError(RuntimeError):
    """The model cannot be built, with the reason.

    Raised rather than returning an empty model: a risk screen showing zero
    volatility and one showing "no data" must not look the same, and zero is
    the more believable of the two.
    """


@dataclass(frozen=True)
class PortfolioModel:
    """A fitted risk model and the window it describes."""

    covariance: FactorCovariance
    exposures: Exposures
    first_date: date
    last_date: date

    @property
    def observations(self) -> int:
        return self.exposures.observations


def _series(store: DuckStore, namespace: str, *, as_of: datetime) -> dict[str, dict[date, float]]:
    """Every `TOT_RETURN` series in a namespace, as of a knowledge time.

    Through the store's own point-in-time reads rather than a query of its
    own: a risk model built off a different visibility rule from the rest of
    the system would answer "what was the risk on Tuesday" differently from
    every other screen (I2).
    """
    out: dict[str, dict[date, float]] = {}
    for subject in store.subjects_with_prefix(f"{namespace}:", as_of=as_of):
        name = str(subject).split(":", 1)[1]
        series = {
            fact.effective_from: float(fact.value)
            for fact in store.read(TUID(str(subject)), "TOT_RETURN", as_of=as_of)
            if isinstance(fact.value, float | int)
        }
        if series:
            out[name] = series
    return out


#: Namespace the Twelve Data adapter writes prices under.
EQUITY_NS = "equity"


def equity_returns(store: DuckStore, *, as_of: datetime) -> dict[str, dict[date, float]]:
    """Daily returns derived from stored `ADJ_CLOSE` prices.

    The bridge between the price adapter and the model. `ADJ_CLOSE` is a
    total return already — split- and dividend-adjusted, established by
    measurement rather than by the vendor's say-so — so a simple
    price relative *is* the total return for the day and no dividend term is
    added. Adding one here would double-count it.

    **Consecutive stored days, not calendar days.** A return is computed
    between adjacent observations in the series, whatever the gap: over a
    weekend that is correct, and over a missing day it is a two-day return
    labelled with one date. The alternative is worse — interpolating a price
    to fill the hole would invent an observation and then compute two
    returns from it, both fabricated. The gap is left visible in the dates
    instead, and `ReturnPanel.aligned` drops any date the factors do not
    also have.

    **A non-positive price yields no return.** It is a data error, not a
    company worth nothing, and `log`/division would either raise or produce
    a number that looks like a -100% day.
    """
    out: dict[str, dict[date, float]] = {}
    for subject in store.subjects_with_prefix(f"{EQUITY_NS}:", as_of=as_of):
        name = str(subject).split(":", 1)[1]
        prices = sorted(
            (fact.effective_from, float(fact.value))
            for fact in store.read(TUID(str(subject)), "ADJ_CLOSE", as_of=as_of)
            if isinstance(fact.value, float | int)
        )
        series = {
            day: (price / previous) - 1.0
            for (_, previous), (day, price) in itertools.pairwise(prices)
            if previous > 0.0 and price > 0.0
        }
        if series:
            out[name] = series
    return out


def build_factor_model(
    store: DuckStore, *, as_of: datetime, window_days: int = WINDOW_DAYS
) -> PortfolioModel:
    """Fit the model on the most recent `window_days` of stored returns."""
    factors = _series(store, FACTOR_NS, as_of=as_of)
    # Contributed portfolio series first, then equities derived from stored
    # prices. Contributed wins on a name collision: a series somebody stated
    # outranks one this system inferred, and silently preferring the derived
    # one would overwrite an input with a calculation.
    assets = {**equity_returns(store, as_of=as_of), **_series(store, ASSET_NS, as_of=as_of)}

    missing = [name for name in (*FACTORS, RISK_FREE) if name not in factors]
    if missing:
        raise FactorModelUnavailableError(
            f"no return series for {', '.join(missing)}. Ingest the Ken French factors "
            "(`treble ingest --source frenchdata`) before asking for portfolio risk"
        )
    if not assets:
        raise FactorModelUnavailableError(
            f"no assets under `{ASSET_NS}:` and no prices under `{EQUITY_NS}:`; there is "
            "nothing to hold, let alone to decompose"
        )

    risk_free = factors[RISK_FREE]
    chosen = {name: factors[name] for name in FACTORS}
    factor_panel = ReturnPanel.aligned(chosen, names=FACTORS)
    window = factor_panel.dates[-window_days:]
    if len(window) < MIN_OBSERVATIONS:
        raise FactorModelUnavailableError(
            f"only {len(window)} common factor observations; {MIN_OBSERVATIONS} is the "
            "fewest a covariance over these factors can be estimated from"
        )
    days = set(window)

    factor_panel = ReturnPanel.aligned(
        {name: {d: v for d, v in series.items() if d in days} for name, series in chosen.items()},
        names=FACTORS,
    )
    # Excess returns, and only assets that cover the whole window: a beta
    # estimated over a different period from its neighbours' would sit in the
    # same table looking comparable.
    excess = {
        name: {day: value - risk_free[day] for day, value in series.items() if day in risk_free}
        for name, series in assets.items()
    }
    complete = {name: s for name, s in excess.items() if days <= set(s)}
    if not complete:
        raise FactorModelUnavailableError(
            f"none of the {len(assets)} assets covers the full {len(window)}-day window, so "
            "no two exposures would describe the same period"
        )
    asset_panel = ReturnPanel.aligned(
        {name: {d: v for d, v in s.items() if d in days} for name, s in complete.items()},
        names=tuple(sorted(complete)),
    )

    covariance = factor_covariance.__wrapped__(factor_panel)  # type: ignore[attr-defined]
    exposures = estimate_exposures.__wrapped__(asset_panel, factor_panel)  # type: ignore[attr-defined]
    return PortfolioModel(
        covariance=covariance,
        exposures=exposures,
        first_date=window[0],
        last_date=window[-1],
    )


def template_portfolio(model: PortfolioModel) -> dict[str, float]:
    """The portfolio `PORT` shows: equally weighted across every asset fitted.

    **A template, not a holding.** This system books no positions, so the
    screen shows a defined portfolio rather than inventing one — the same
    choice `SWPM` makes with its template trade, and the caption says so on
    the screen rather than only here.

    Equal weights specifically, because they are the one set that is not an
    opinion. Any other weighting would be a view this system has no basis
    for, presented on screen as though it were someone's book.
    """
    count = len(model.exposures.assets)
    if not count:
        raise FactorModelUnavailableError("no assets in the fitted model to weight")
    return dict.fromkeys(model.exposures.assets, 1.0 / count)


__all__ = [
    "ASSET_NS",
    "EQUITY_NS",
    "FACTORS",
    "FACTOR_NS",
    "RISK_FREE",
    "WINDOW_DAYS",
    "FactorModelUnavailableError",
    "PortfolioModel",
    "build_factor_model",
    "equity_returns",
    "template_portfolio",
]
