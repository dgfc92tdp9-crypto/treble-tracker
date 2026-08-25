"""Fund positions, summed back to the instrument they are held in.

`ingest.nport` keys a holding's own numbers to `pos:<fund>:<instrument>`
rather than to the instrument, because two funds holding one bond report two
different values and a single subject can only show one. Screens in this
repository present the store as one book, so they want the whole holding:
this module does that addition in one place.

**Summed, not picked.** The defect being repaired was a silent choice between
funds — the visibility window returned whichever filing was fetched last, so
a bond held at $1.87bn, $35.0m and $4.39m read as $4.39m. Adding them is the
answer that matches what these screens claim to show; picking one is what
went wrong.

**Only additive fields are summed.** `pctVal` is a percentage of *a fund's*
portfolio and adding two of them produces a number that is not a percentage
of anything, so it is deliberately absent here and stays readable per
position for anyone who wants it.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from treble.core.identifiers import (
    POSITION_PREFIX,
    TUID,
    parse_position_subject,
    position_prefix_for,
)
from treble.store.duck import DuckStore

#: Position fields that mean something when added across funds. A par balance
#: and a market value are quantities; a percentage of portfolio is not.
SUMMED_FIELDS: tuple[str, ...] = ("nport:valUSD", "nport:balance")


def totals_by_instrument(
    store: DuckStore, *, as_of: datetime
) -> dict[tuple[str, date], dict[str, float]]:
    """Every instrument's summed position, by report date.

    Keyed by `(instrument subject, effective date)` so a caller already
    walking instruments and dates can look up without a second sweep.
    """
    totals: dict[tuple[str, date], dict[str, float]] = defaultdict(dict)
    for subject in store.subjects_with_prefix(POSITION_PREFIX, as_of=as_of):
        parsed = parse_position_subject(subject)
        if parsed is None:
            continue
        _, instrument = parsed
        for fact in store.subject_facts(TUID(str(subject)), as_of=as_of):
            if fact.field not in SUMMED_FIELDS or not isinstance(fact.value, int | float):
                continue
            bucket = totals[(str(instrument), fact.effective_from)]
            bucket[fact.field] = bucket.get(fact.field, 0.0) + float(fact.value)
    return dict(totals)


def totals_for(store: DuckStore, instrument: TUID | str, *, as_of: datetime) -> dict[str, float]:
    """One instrument's summed position, on its most recent report date.

    For callers holding a single instrument rather than sweeping the store.
    Prefix-matches that instrument's positions rather than building every
    instrument's totals and discarding the rest — the callers are inside
    per-bond loops, and the discarded version is quadratic in the store.

    An empty mapping when no fund reports it, which is a real answer rather
    than an error.
    """
    per_date: dict[date, dict[str, float]] = defaultdict(dict)
    for subject in store.subjects_with_prefix(position_prefix_for(instrument), as_of=as_of):
        for fact in store.subject_facts(TUID(str(subject)), as_of=as_of):
            if fact.field not in SUMMED_FIELDS or not isinstance(fact.value, int | float):
                continue
            bucket = per_date[fact.effective_from]
            bucket[fact.field] = bucket.get(fact.field, 0.0) + float(fact.value)
    if not per_date:
        return {}
    return per_date[max(per_date)]
