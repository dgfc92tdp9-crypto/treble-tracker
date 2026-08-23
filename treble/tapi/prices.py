"""Which subject carries a security's traded series, and which field it is.

Two things the rest of the system did not have a name for, and `GP`/`HP`
need both.

**The listing is not the company.** `AAPL US Equity` resolves to
`cik:0000320193`, because that is where EDGAR's fundamentals are written
and it is the right answer for `DES` and `FA`. The price series is written
by the Twelve Data adapter under `equity:AAPL`. Those are different
subjects on purpose: a company files once and may be listed several
times, so fundamentals belong to the filer and a price belongs to a
listing. Collapsing them would make the second listing unrepresentable.

Nothing in the store links the two, and nothing needs to: the user typed
the ticker, and the ticker *is* the listing's subject key. So this
resolves the listing from the query rather than inferring it from the
company — no traversal, no missing edge.

**The series is not always called the same thing.** FRED publishes index
levels as `PX_LAST`. Twelve Data's daily `close` is split *and dividend*
adjusted, so the adapter stores it as `ADJ_CLOSE` and says why at length:
a column called PX_LAST that is silently a total return is the kind of
thing a later reader regresses against a factor model without checking,
and dividend yield then arrives as alpha.

So this does **not** map one onto the other. It reports which field it
found, and :func:`price_basis` puts that on the screen beside the chart.
A reader looking at an equity line in `GP` can see it is a total-return
series; a reader looking at an index line can see it is not. Silently
substituting would have been three lines shorter and would have undone
the adapter's whole argument.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from treble.core.identifiers import TUID
from treble.store.duck import DuckStore

#: Where the equity price adapter writes. Shared with `ingest/twelvedata.py`
#: through this constant rather than spelled twice.
LISTING_PREFIX = "equity:"

#: Candidate daily-series fields, most specific first. Order matters: a
#: subject carrying both would be reported as the adjusted series, which
#: is the more conservative label — calling a total return a last price
#: misleads, calling a last price a total return merely over-warns.
PRICE_FIELDS: tuple[tuple[str, str], ...] = (
    ("ADJ_CLOSE", "daily close, split and dividend adjusted (total return)"),
    ("PX_LAST", "published level, not adjusted"),
)


class NoPriceSeriesError(LookupError):
    """This subject carries no daily series under any known field."""


@dataclass(frozen=True)
class PriceSeries:
    """A daily series, and an honest account of what it is."""

    subject: TUID
    field: str
    basis: str
    points: tuple[tuple[date, float], ...]

    @property
    def last(self) -> float | None:
        return self.points[-1][1] if self.points else None

    @property
    def last_date(self) -> date | None:
        return self.points[-1][0] if self.points else None

    @property
    def first_date(self) -> date | None:
        return self.points[0][0] if self.points else None


def listing_subject(ticker: str) -> TUID:
    return TUID(f"{LISTING_PREFIX}{ticker.upper()}")


def price_subject(store: DuckStore, *, ticker: str | None, resolve: Callable[[], TUID]) -> TUID:
    """The subject holding the traded series: the listing if there is one.

    Falls back to the security master, so an index — which has no separate
    listing — keeps working unchanged.

    **The listing is tried first, and the fallback is lazy.** A price does
    not need a filer, and requiring one refuses securities whose prices
    are sitting in the store. `BRK.B` is the case that showed it: EDGAR's
    company index spells the class B share `BRK-B` and Twelve Data spells
    it `BRK.B`, so resolving the company first raised
    `SecurityNotFoundError` for a ticker with 20,000 price facts under
    `equity:BRK.B`. Two vendors disagreeing about punctuation in a
    share-class suffix is normal, and it must not cost the chart.
    """
    if ticker:
        listing = listing_subject(ticker)
        if store.has_subject(listing):
            return listing
    return resolve()


def price_series(
    store: DuckStore, subject: TUID, *, as_of: datetime, limit: int | None = None
) -> PriceSeries:
    """The daily series on a subject, under whichever field carries it.

    ``limit`` keeps the most *recent* n points, not the first n. A chart
    truncated from the wrong end shows 2006 and calls it current.
    """
    for field, basis in PRICE_FIELDS:
        facts = store.history(subject, field, as_of=as_of)
        if not facts:
            continue
        points = tuple(
            (fact.effective_from, float(fact.value))
            for fact in sorted(facts, key=lambda f: f.effective_from)
            if isinstance(fact.value, (int, float))
        )
        if not points:
            continue
        return PriceSeries(
            subject=subject,
            field=field,
            basis=basis,
            points=points[-limit:] if limit else points,
        )
    raise NoPriceSeriesError(
        f"{subject} carries no daily series: looked for "
        f"{', '.join(name for name, _ in PRICE_FIELDS)}"
    )


def price_basis(series: PriceSeries) -> tuple[tuple[str, str], ...]:
    """What the chart above is, in rows a screen can print.

    On the screen rather than in a docstring, because the difference
    between a total-return series and a price series is invisible in the
    line itself and changes what the line means.
    """
    # Ordered by what a reader needs first, not by what is easiest to
    # compute. `GP` shows a four-row window of this and `HP` shows all of
    # it, so anything below the fourth row is invisible on `GP` — which is
    # why the series and its basis lead, and the provenance-ish rows
    # (subject, span, count) come after the number itself.
    return (
        ("Series", series.field),
        ("Basis", series.basis),
        ("Last", f"{series.last:,.4g}" if series.last is not None else "—"),
        ("As of", series.last_date.isoformat() if series.last_date else "—"),
        ("Subject", str(series.subject)),
        ("From", series.first_date.isoformat() if series.first_date else "—"),
        ("Observations", f"{len(series.points):,}"),
    )


__all__ = [
    "LISTING_PREFIX",
    "PRICE_FIELDS",
    "NoPriceSeriesError",
    "PriceSeries",
    "listing_subject",
    "price_basis",
    "price_series",
    "price_subject",
]
