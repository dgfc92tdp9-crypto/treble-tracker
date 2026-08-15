"""`OAS1` — what callability would cost, under a stated structure (§10.2).

**This screen does not report your bonds' option-adjusted spreads, and it
cannot.** N-PORT publishes `maturityDt`, `couponKind`, `isPaidKind` and
thirty other fields, and **no call schedule** — the schema has none. Without
one, OAS is identically the Z-spread, so an `OAS1` built on this store would
either repeat `SPRD`'s Z column under a new heading or price a call schedule
somebody invented and present it as the bond's terms.

So it is a *sensitivity*: if this bond were callable on the structure named
in the row, at the volatility named in the row, the option would cost this
much. Every row is a conditional, and the structure and the vol are both
columns rather than hidden parameters — because the moment either becomes a
default the answer starts reading as a measurement.

That is the same treatment ADR-0003 already gives the volatility: Phase 1's
lattice OAS takes vol as an explicit user-supplied parameter stamped into
the I3 envelope, precisely so a number nobody chose cannot end up on a
screen. This extends it to the call schedule.

**Option cost is the product, not OAS.** OAS alone invites comparison with
the Z-spread on `SPRD` as though they measured the same bond; the difference
between them is the thing callability is worth, and it is the only figure
here that survives being wrong about the exact call date. It must be
non-negative — a call right belongs to the issuer, so it can only make the
holder's spread narrower — and a negative one means the lattice, the
schedule or the vol is wrong rather than that the market is odd.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from treble.analytics.bonds.spec import CallSchedule, FixedBondSpec
from treble.analytics.curves.bootstrap import Curve
from treble.store.duck import DuckStore
from treble.tapi.spreads import BondNotPriceableError, bond_pricing_inputs

#: Hull-White short-rate volatilities, absolute. Three rather than one so
#: the reader sees the shape of the dependence: an option cost that barely
#: moves across this range is a call far out of the money, and one that
#: triples is a bond whose value is mostly optionality.
VOLATILITIES: tuple[float, ...] = (0.005, 0.010, 0.015)

#: Call structures, as years of protection before maturity. A corporate par
#: call is usually a few months before maturity and an NC-x structure is
#: quoted by its non-call period, so both shapes are represented.
#:
#: Expressed relative to maturity rather than as absolute dates: the same
#: row then means the same thing for a 2027 bond and a 2055 one, and a
#: reader comparing two bonds is comparing structures rather than calendars.
STRUCTURES: tuple[tuple[str, float], ...] = (
    ("par call, 0.25y before maturity", 0.25),
    ("callable at par, last 1y", 1.0),
    ("callable at par, last 3y", 3.0),
)

#: Below this the lattice is pricing an option on a bond that has already
#: nearly matured, and the answer is dominated by the step size rather than
#: by the structure.
MIN_OPTION_YEARS = 0.2


class OptionCostUnavailableError(ValueError):
    """No lattice result could be produced for this bond."""


@dataclass(frozen=True)
class OptionCostRow:
    """One (structure, volatility) cell of the sensitivity."""

    structure: str
    protection_years: float
    volatility: float
    #: The option-adjusted spread under this hypothetical structure, bp.
    oas_bp: float
    #: Z less OAS: what the call right would cost the holder, bp. Never
    #: negative for a genuine call — see the module docstring.
    option_cost_bp: float


@dataclass(frozen=True)
class OptionCostGrid:
    """A bond, its bullet Z-spread, and the sensitivity around it."""

    identifier: str
    issuer: str | None
    maturity: date
    coupon_pct: float
    price: float
    report_date: date
    curve_date: date
    #: The bullet Z-spread the option cost is measured from. The bond as it
    #: actually is; every row below is the bond as it is not.
    z_spread_bp: float
    rows: tuple[OptionCostRow, ...]
    #: Structures skipped, and why — a grid that silently dropped a row
    #: would look like a structure that cost nothing.
    skipped: tuple[tuple[str, str], ...]


def _call_from(spec: FixedBondSpec, protection_years: float, report: date) -> date | None:
    """First call date for a structure, or None if it does not fit.

    Returns None rather than clamping to the report date. A structure whose
    call has already passed is not a shorter option, it is not that
    structure at all, and pricing it as an immediately-callable bond would
    put a number in the row that answers a different question.
    """
    total_years = (spec.maturity - report).days / 365.25
    if total_years - protection_years <= MIN_OPTION_YEARS:
        return None
    days = int(protection_years * 365.25)
    return date.fromordinal(spec.maturity.toordinal() - days)


def option_cost_grid(
    store: DuckStore,
    *,
    identifier: str,
    as_of: datetime,
    volatilities: tuple[float, ...] = VOLATILITIES,
    structures: tuple[tuple[str, float], ...] = STRUCTURES,
) -> OptionCostGrid:
    """Price the bond bullet, then under each hypothetical call structure."""
    from treble.analytics.bonds.callable import oas as lattice_oas
    from treble.analytics.bonds.pricing import z_spread
    from treble.tapi.spreads import _swap_curve_dates
    from treble.tapi.swap_market import SwapMarketUnavailableError, build_usd_discount_curve

    inputs = bond_pricing_inputs(store, identifier=identifier, as_of=as_of)
    spec, price, report = inputs.spec, inputs.price, inputs.report_date

    curve: Curve | None = None
    curve_date: date | None = None
    for candidate in _swap_curve_dates(store, as_of=as_of):
        try:
            curve = build_usd_discount_curve(store, as_of=as_of, report_date=candidate)
        except SwapMarketUnavailableError:
            continue
        curve_date = candidate
        break
    if curve is None or curve_date is None:
        raise OptionCostUnavailableError(
            "no USD discount curve on any stored date; the lattice discounts on it and "
            "an option cost measured against nothing is not a cost"
        )

    try:
        bullet_z = z_spread.__wrapped__(spec, price, curve, as_of=report) * 10_000.0  # type: ignore[attr-defined]
    except ValueError as error:
        # The solver brackets between -500bp and +5,000bp. Outside it the
        # mark is distressed or stale, and every option cost below would be
        # measured from a number that does not exist.
        raise OptionCostUnavailableError(
            f"no bullet Z-spread for {identifier}: {error}. Option cost is Z less OAS, so "
            "there is nothing to measure from"
        ) from error

    rows: list[OptionCostRow] = []
    skipped: list[tuple[str, str]] = []
    for label, protection in structures:
        first_call = _call_from(spec, protection, report)
        if first_call is None:
            skipped.append(
                (label, f"leaves under {MIN_OPTION_YEARS:g}y of option life on this maturity")
            )
            continue
        callable_spec = spec.model_copy(
            update={"calls": (CallSchedule(start=first_call, price=100.0),)}
        )
        for vol in volatilities:
            try:
                spread = lattice_oas.__wrapped__(  # type: ignore[attr-defined]
                    callable_spec, price, curve, as_of=report, volatility=vol
                )
            except (RuntimeError, ValueError) as error:
                skipped.append((f"{label} @ {vol:.2%}", f"lattice refused: {error}"))
                continue
            oas_bp = spread * 10_000.0
            rows.append(
                OptionCostRow(
                    structure=label,
                    protection_years=protection,
                    volatility=vol,
                    oas_bp=oas_bp,
                    option_cost_bp=bullet_z - oas_bp,
                )
            )

    if not rows:
        raise OptionCostUnavailableError(
            f"no structure priced for {identifier}: {len(skipped)} skipped. A bond with "
            "months left has no meaningful call structure to test"
        )
    return OptionCostGrid(
        identifier=identifier,
        issuer=inputs.issuer,
        maturity=spec.maturity,
        coupon_pct=inputs.coupon_pct,
        price=price,
        report_date=report,
        curve_date=curve_date,
        z_spread_bp=bullet_z,
        rows=tuple(rows),
        skipped=tuple(skipped),
    )


__all__ = [
    "MIN_OPTION_YEARS",
    "STRUCTURES",
    "VOLATILITIES",
    "BondNotPriceableError",
    "OptionCostGrid",
    "OptionCostRow",
    "OptionCostUnavailableError",
    "option_cost_grid",
]
