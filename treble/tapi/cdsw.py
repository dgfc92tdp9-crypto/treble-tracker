"""`CDSW` — a reference entity's CDS curve, and what it prices to (§13).

The pricer in `analytics.credit.cds` has been ISDA-validated since Phase 2
began and had no data to run on. `ingest.dtcc_credit` now supplies it: the
SEC security-based swap tape, reduced to curve points per reference entity
and tenor.

## What this screen may and may not say

The tape quotes a spread on 62 prints in 586. Standard contracts trade at a
fixed coupon with an upfront payment, so most of the file gives coupon and
points-upfront and leaves the spread to be implied.

This binding shows **only what was observed**, and prices from it where a
spread was quoted. Where the tape gave points-upfront instead, the row says
so and the spread column is blank — implying it needs a discount curve and a
solve, which is `analytics` work rather than a display concern, and inventing
it here would put a number on the screen that no source stated.

Where a notional was capped (`5,000,000+`), the upfront is absent by
construction — see `dtcc_credit.notional_is_capped`. The trade count still
shows, so a thin point looks thin.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from treble.analytics.credit.cds import CdsSpec, hazard_from_spread, price_cds
from treble.core.identifiers import TUID
from treble.store.duck import DuckStore

#: Header rows, so an empty screen still says what it would have shown.
CURVE_HEADER: tuple[str, ...] = ("Tenor", "Spread bp", "Coupon bp", "Upfront %", "Trades", "Capped")
PRICING_HEADER: tuple[str, ...] = ("Tenor", "Hazard bp", "Risky PV01", "Upfront %", "Model")

#: The flat discount rate used until `SWPM`'s multi-curve, CSA-aware
#: discounting is wired through. Named and shown on the method pane rather
#: than hidden: a CDS upfront is sensitive to it, and a reader who cannot
#: see the assumption cannot judge the number.
FLAT_DISCOUNT = 0.04

Row = tuple[str | float | int | None, ...]


def _number(value: object) -> float | None:
    """A stored value as a number, or None.

    `subject_facts` returns every value kind a fact can hold; a count that
    arrived as text is not a count, and rendering it as one would put a
    string in a numeric column.
    """
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _tenor_years(subject: str) -> int:
    return int(subject.rsplit(":", 1)[-1].removesuffix("Y"))


def entity_curve(store: DuckStore, entity: str, *, as_of: datetime) -> list[Row]:
    """One reference entity's observed curve, tenor by tenor."""
    rows: list[Row] = [CURVE_HEADER]
    for subject in sorted(store.subjects_with_prefix(f"{entity}:", as_of=as_of), key=_tenor_years):
        facts = {f.field: f.value for f in store.subject_facts(TUID(subject), as_of=as_of)}
        spread = facts.get("PAR_SPREAD")
        coupon = facts.get("CDS_COUPON")
        upfront = facts.get("UPFRONT_FRACTION")
        rows.append(
            (
                f"{_tenor_years(subject)}Y",
                round(spread * 1e4, 1) if isinstance(spread, float) else None,
                round(coupon * 1e4, 1) if isinstance(coupon, float) else None,
                round(upfront * 100, 3) if isinstance(upfront, float) else None,
                _number(facts.get("TRADE_COUNT")),
                _number(facts.get("CAPPED_TRADE_COUNT")),
            )
        )
    if len(rows) == 1:
        rows.append(("no CDS prints for this entity in the store", None, None, None, None, None))
    return rows


def entity_pricing(
    store: DuckStore, entity: str, *, as_of: datetime, today: date | None = None
) -> list[Row]:
    """The standard contract priced off each observed spread.

    A tenor whose spread was not quoted is listed with a reason rather than
    omitted: a curve that silently showed three of five tenors would read as
    a three-tenor curve.
    """
    valuation = today or as_of.date()
    rows: list[Row] = [PRICING_HEADER]
    for subject in sorted(store.subjects_with_prefix(f"{entity}:", as_of=as_of), key=_tenor_years):
        years = _tenor_years(subject)
        facts = {f.field: f.value for f in store.subject_facts(TUID(subject), as_of=as_of)}
        spread, coupon = facts.get("PAR_SPREAD"), facts.get("CDS_COUPON")
        if not isinstance(spread, float):
            rows.append((f"{years}Y", None, None, None, "no spread quoted; upfront only"))
            continue
        hazard = hazard_from_spread(spread)
        spec = CdsSpec(
            notional=10_000_000.0,
            coupon=coupon if isinstance(coupon, float) else 0.01,
            trade_date=valuation,
            maturity=date(valuation.year + years, valuation.month, valuation.day),
        )
        priced = price_cds(spec, hazard.value, FLAT_DISCOUNT)
        rows.append(
            (
                f"{years}Y",
                round(hazard.value * 1e4, 1),
                round(priced.value.risky_pv01, 2),
                round(priced.value.upfront / spec.notional * 100, 3),
                priced.model_id,
            )
        )
    if len(rows) == 1:
        rows.append(("nothing to price: no tenors held", None, None, None, None))
    return rows


def method(entity: str) -> list[Row]:
    """What the numbers rest on, stated where they are shown."""
    return [
        ("Item", "Value"),
        ("Reference entity", entity),
        ("Source", "DTCC SEC security-based swap repository, daily cumulative credit"),
        ("Spread", "as quoted on the tape; blank where only an upfront was reported"),
        ("Hazard", "credit triangle h = s / (1 - R), an approximation and named as one"),
        ("Recovery", "40% standard assumption"),
        ("Discount", f"flat {FLAT_DISCOUNT:.1%} until SWPM's multi-curve discounting is wired"),
        ("Capped notionals", "excluded from upfront; counted in Capped"),
        ("Redistribution", "restricted — Markit RED codes and unverified DTCC terms"),
    ]


def default_entity(store: DuckStore, *, as_of: datetime | None = None) -> str | None:
    """The entity with the most observed tenors, for an unparameterised screen.

    Deliberate rather than alphabetical: a screen opening on the deepest
    curve shows what the data can do, and one opening on `cds:isin:US0001...`
    shows a single point and reads as broken.
    """
    when = as_of or datetime.now(UTC)
    counts: dict[str, int] = {}
    for subject in store.subjects_with_prefix("cds:", as_of=when):
        parts = subject.split(":")
        if len(parts) == 4:
            counts[":".join(parts[:3])] = counts.get(":".join(parts[:3]), 0) + 1
    if not counts:
        return None
    return max(sorted(counts), key=lambda key: counts[key])


__all__ = [
    "CURVE_HEADER",
    "FLAT_DISCOUNT",
    "PRICING_HEADER",
    "default_entity",
    "entity_curve",
    "entity_pricing",
    "method",
]
