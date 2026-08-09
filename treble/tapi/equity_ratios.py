"""Fundamental ratios from stored XBRL (spec §14.1).

`analytics/equity/ratios.py` has held margins, returns, leverage and growth
since Phase 1 and nothing called them. The reachability gate named it. This
is the caller: stored `us-gaap:*` facts in, ratios out.

**Which tag supplied each input travels with the answer.** That is the whole
difficulty here, not the arithmetic. Filers do not agree on a revenue tag:
measured on this store, 2,349 subjects report `Revenues` and 3,184 report
`RevenueFromContractWithCustomerExcludingAssessedTax`. A margin computed
from one is not comparable with a margin computed from the other — the
second excludes assessed taxes the first may include — so a service that
silently picked whichever it found would produce a screen full of numbers
that look like a peer comparison and are not one.

:class:`RatioSet` therefore carries `sources`, and the `FA` drill-down is
expected to show it. §14.1 says unmapped extension tags are surfaced rather
than dropped; this is the same principle applied to the mapped ones.

**One period, or nothing.** Every input is taken from the same fiscal
period. Mixing this year's revenue with last year's equity produces a
return on equity that is wrong by however much the balance sheet moved, and
wrong in a way no reader can see. Where a concept has no observation in the
chosen period the ratio is absent rather than computed from the nearest
available date.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime

from treble.analytics.equity.ratios import (
    UndefinedRatioError,
    gross_margin,
    leverage,
    net_margin,
    operating_margin,
    return_on_assets,
    return_on_equity,
)
from treble.core.identifiers import TUID
from treble.store.duck import DuckStore

#: Ordered tag preference per concept. First match wins, and the winner is
#: reported. Ordering is by specificity rather than by popularity: the
#: contract-revenue tags say exactly what they measure, where `Revenues` is
#: whatever the filer decided to call the top line.
TAG_PREFERENCE: dict[str, tuple[str, ...]] = {
    "revenue": (
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
        "us-gaap:Revenues",
    ),
    "gross_profit": ("us-gaap:GrossProfit",),
    "operating_income": ("us-gaap:OperatingIncomeLoss",),
    "net_income": ("us-gaap:NetIncomeLoss",),
    "equity": (
        "us-gaap:StockholdersEquity",
        "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "assets": ("us-gaap:Assets",),
}

#: Unit suffix the store appends to XBRL fields.
_UNIT = ":USD"


class RatiosUnavailableError(RuntimeError):
    """Nothing in the store supports a ratio for this subject."""


@dataclass(frozen=True)
class RatioSet:
    """Ratios for one filer at one period, and what they were built from."""

    subject: str
    period: date
    ratios: dict[str, float]
    #: Concept -> the XBRL tag that supplied it. Carried because a margin
    #: from `Revenues` and one from `RevenueFromContractWithCustomer...`
    #: are not comparable, and a screen showing only the percentage would
    #: present them as though they were.
    sources: dict[str, str] = field(default_factory=dict)
    #: Concepts no preferred tag supplied. Named rather than omitted: a
    #: missing ratio and a ratio nobody asked for look identical on a
    #: screen, and only the first is a data gap.
    missing: tuple[str, ...] = ()


def _observations(
    store: DuckStore, subject: TUID, *, as_of: datetime
) -> dict[str, dict[date, tuple[float, str]]]:
    """`{concept: {period: (value, tag)}}` for the preferred tags only."""
    out: dict[str, dict[date, tuple[float, str]]] = {}
    for concept, tags in TAG_PREFERENCE.items():
        found: dict[date, tuple[float, str]] = {}
        for tag in tags:
            for fact in store.read(subject, tag + _UNIT, as_of=as_of):
                if not isinstance(fact.value, int | float):
                    continue
                # First tag in preference order wins for a given period; a
                # later one does not overwrite it.
                found.setdefault(fact.effective_from, (float(fact.value), tag))
        if found:
            out[concept] = found
    return out


def ratios_for(
    store: DuckStore, subject: TUID, *, as_of: datetime, period: date | None = None
) -> RatioSet:
    """Every ratio the stored fundamentals support, for one fiscal period.

    `period` defaults to the most recent date on which *net income* is
    reported, because that is the concept most ratios here depend on and
    picking the latest date across all concepts would guarantee a mixed
    period.
    """
    observed = _observations(store, subject, as_of=as_of)
    if "net_income" not in observed:
        raise RatiosUnavailableError(
            f"{subject}: no {' or '.join(TAG_PREFERENCE['net_income'])} in the store, so "
            "no ratio here can be computed"
        )
    chosen = period or max(observed["net_income"])

    values: dict[str, float] = {}
    sources: dict[str, str] = {}
    missing: list[str] = []
    for concept in TAG_PREFERENCE:
        entry = observed.get(concept, {}).get(chosen)
        if entry is None:
            missing.append(concept)
            continue
        values[concept], sources[concept] = entry

    ratios: dict[str, float] = {}
    computations: tuple[tuple[str, Callable[[float, float], float], tuple[str, str]], ...] = (
        ("gross_margin", gross_margin.__wrapped__, ("gross_profit", "revenue")),  # type: ignore[attr-defined]
        ("operating_margin", operating_margin.__wrapped__, ("operating_income", "revenue")),  # type: ignore[attr-defined]
        ("net_margin", net_margin.__wrapped__, ("net_income", "revenue")),  # type: ignore[attr-defined]
        ("return_on_equity", return_on_equity.__wrapped__, ("net_income", "equity")),  # type: ignore[attr-defined]
        ("return_on_assets", return_on_assets.__wrapped__, ("net_income", "assets")),  # type: ignore[attr-defined]
        ("leverage", leverage.__wrapped__, ("assets", "equity")),  # type: ignore[attr-defined]
    )
    for name, fn, args in computations:
        if any(a not in values for a in args):
            continue
        try:
            ratios[name] = fn(values[args[0]], values[args[1]])
        except UndefinedRatioError:
            # A zero denominator is a filing that cannot support the ratio,
            # not a ratio of zero. Absent, and the concept is not listed as
            # missing because it was present and unusable.
            continue

    if not ratios:
        raise RatiosUnavailableError(
            f"{subject}: fundamentals exist for {chosen} but no pair supports a ratio"
        )
    return RatioSet(
        subject=str(subject),
        period=chosen,
        ratios=ratios,
        sources=sources,
        missing=tuple(missing),
    )


__all__ = ["TAG_PREFERENCE", "RatioSet", "RatiosUnavailableError", "ratios_for"]
