"""The `VCUB` swaption surface, assembled from stored prints (spec §11.3).

Turns `swaption:*` facts — the DTCC adapter's output — into a fitted
volatility grid, by solving each print against the curve environment of the
day it printed on.

**Each day on its own curve.** A print from the 13th valued off the 31st's
curve carries an eighteen-day-stale forward, which misplaces its moneyness
and inflates its implied volatility most where vega is smallest. Measured:
that mistake alone took node dispersion from under 20% to 84-105%. So this
builds a curve environment per trading day and refuses the day rather than
substituting another's.

**Normal vol, not lognormal.** EUR and JPY swaptions are quoted in normal
volatility, and Bachelier is defined at or below a zero forward where Black
is not. The screen says which convention its numbers are in, because 130 is
a plausible number in basis points and an implausible one in percent.

Lives in `tapi` because reading the store is TAPI's job (I7); the pricing,
the vol solve and the grid are all `analytics`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from treble.analytics.derivatives.csa import CsaTerms
from treble.analytics.derivatives.swap import SwapSpec, price_swap, swap_par_rate
from treble.analytics.vol.surface import VolSurface, build_surface
from treble.analytics.vol.swaption import ImpliedVolError, SwaptionQuote, implied_normal_vol
from treble.core.identifiers import TUID
from treble.store.duck import DuckStore
from treble.tapi.swap_market import (
    DISCOUNT_CURVE,
    FORECAST_CURVE,
    SwapMarketUnavailableError,
    build_swap_market,
)

#: The currency this surface is built for. EUR because it is the currency
#: whose discount and forecast curves this repository builds from the same
#: tape the swaptions come from — a print in a currency with no curve cannot
#: be turned into a volatility at all.
CURRENCY = "EUR"


class VolSurfaceUnavailableError(RuntimeError):
    """No surface could be built, with the counts that led there."""


@dataclass(frozen=True)
class SurfaceBuild:
    """A fitted surface and what it cost to fit."""

    surface: VolSurface
    prints_read: int
    prints_solved: int
    days_used: int
    days_without_curves: int

    @property
    def solve_rate(self) -> float:
        return self.prints_solved / self.prints_read if self.prints_read else 0.0


def build_vol_surface(store: DuckStore, *, as_of: datetime, pool_days: bool = True) -> SurfaceBuild:
    """Solve every stored swaption print against its own day's curves."""
    subjects = store.subjects_with_prefix(f"swaption:{CURRENCY}:", as_of=as_of)
    if not subjects:
        raise VolSurfaceUnavailableError(
            f"no {CURRENCY} swaption prints in the store; the DTCC adapter supplies them "
            "alongside the curve quotes"
        )

    by_day: dict[date, list[dict[str, object]]] = {}
    for subject in subjects:
        facts = store.subject_facts(TUID(str(subject)), as_of=as_of)
        if not facts:
            continue
        by_day.setdefault(facts[0].effective_from, []).append({f.field: f.value for f in facts})

    csa = CsaTerms(collateral_currency=CURRENCY, discount_curve=DISCOUNT_CURVE)
    triples: list[tuple[SwaptionQuote, float, float]] = []
    read = solved = used = missing = 0

    for day in sorted(by_day):
        read += len(by_day[day])
        try:
            market = build_swap_market(store, as_of=as_of, report_date=day)
        except SwapMarketUnavailableError:
            # The day is skipped, never valued off another day's curve.
            missing += 1
            continue
        used += 1
        calendar = market.curves.curve(DISCOUNT_CURVE).config.calendar
        for values in by_day[day]:
            expiry, maturity = values.get("EXPIRY_DATE"), values.get("UNDERLIER_MATURITY")
            strike, premium = values.get("STRIKE"), values.get("PREMIUM_FRACTION")
            if not isinstance(expiry, date) or not isinstance(maturity, date):
                continue
            if not isinstance(strike, float) or not isinstance(premium, float):
                continue
            try:
                spec = SwapSpec(
                    notional=1.0,
                    fixed_rate=0.03,
                    effective=expiry,
                    maturity=maturity,
                    forecast_curve=FORECAST_CURVE,
                    calendar=calendar,
                )
                forward = swap_par_rate.__wrapped__(spec, market.curves, csa)  # type: ignore[attr-defined]
                annuity = price_swap.__wrapped__(spec, market.curves, csa).annuity  # type: ignore[attr-defined]
                volatility = implied_normal_vol.__wrapped__(  # type: ignore[attr-defined]
                    premium_fraction=premium,
                    forward=forward,
                    strike=strike,
                    expiry_years=(expiry - day).days / 365.25,
                    annuity=annuity,
                    payer=bool(values.get("PAYER")),
                )
            except (ImpliedVolError, ValueError):
                # A print the model cannot invert is dropped and counted, not
                # forced: `solve_rate` is what says how much of the tape this
                # surface actually rests on.
                continue
            triples.append(
                (
                    SwaptionQuote(
                        payer=bool(values.get("PAYER")),
                        expiry=expiry,
                        underlier_maturity=maturity,
                        strike=strike,
                        premium_fraction=premium,
                        currency=CURRENCY,
                        traded=day,
                        notional_capped=bool(values.get("NOTIONAL_CAPPED")),
                    ),
                    forward,
                    volatility,
                )
            )
            solved += 1

    if not triples:
        raise VolSurfaceUnavailableError(
            f"{read} prints read over {len(by_day)} days, none solvable: {missing} days had "
            "no curve pair to value against"
        )
    surface = build_surface.__wrapped__(  # type: ignore[attr-defined]
        triples, as_of=max(by_day), currency=CURRENCY, pool_days=pool_days
    )
    return SurfaceBuild(
        surface=surface,
        prints_read=read,
        prints_solved=solved,
        days_used=used,
        days_without_curves=missing,
    )


__all__ = ["CURRENCY", "SurfaceBuild", "VolSurfaceUnavailableError", "build_vol_surface"]
