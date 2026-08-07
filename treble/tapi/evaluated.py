"""Contributed quotes become an evaluated price (spec §15.1-15.3).

The two ends of this pipeline shipped separately and were never joined.
`tapi/contribution.py` accepts a contributor's quote into the book that
`ALLQ` renders; `analytics/tval/evaluate.py` turns observations into a
scored price with an ASC 820 / IFRS 13 level. Nothing in `treble/` imported
the second. It had tests and no caller, so the network could take
contributions and could evaluate observations, and could not do both.

Found by the unread-member gate, which flagged `WeightedObservation.
age_days` as having no reader. The field was the symptom; the whole of
Prong 1 being unreachable was the cause. The ledger recorded a related
decision — that Prong 1 gets no tab, since a permanently empty tab looks
like a broken screen rather than an empty network — and that decision was
about the *screen*. It was not a decision to leave the evaluator with no
way in.

**A mid is not an observation, and neither is a one-way quote.** A
contributor showing only a bid is making a one-way market, which is
information about direction rather than about level. Turning `bid` alone
into a price observation would report the bid as where the bond trades.
Only two-sided quotes become observations, and the count of what was
skipped travels with the result.

**Firmness maps to observation kind, and the mapping is not cosmetic.**
§15.1 weights an executable quote above an indicative one because they are
different evidence. Collapsing them would let a stack of indications
outvote a firm price, which is the failure `ALLQ`'s composite split was
built to prevent — the same distinction, one layer up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from treble.analytics.tval.evaluate import (
    ObservationKind,
    PriceObservation,
    TvalPrice,
    UnpriceableError,
    Weighting,
    evaluate_price,
)
from treble.core.identifiers import TUID
from treble.plant.quotes import Book, Firmness

#: How a contributor's firmness becomes evidence weight. Executable quotes
#: are levels somebody will trade on; indicative ones are opinions about
#: where a trade might happen. §15.1 weights them differently and this is
#: where that distinction survives the crossing.
_KIND_FOR_FIRMNESS = {
    Firmness.EXECUTABLE: ObservationKind.EXECUTABLE_QUOTE,
    Firmness.INDICATIVE: ObservationKind.INDICATIVE_QUOTE,
}


@dataclass(frozen=True)
class EvaluationInputs:
    """What the book yielded, and what it did not."""

    observations: tuple[PriceObservation, ...]
    #: Quotes with only one side. Counted rather than dropped silently: a
    #: book of ten quotes that produced two observations is a different
    #: statement about liquidity from one that produced ten.
    one_way_skipped: int


def observations_from_book(book: Book, *, as_of: datetime) -> EvaluationInputs:
    """Turn a contributed book into price observations.

    The mid of a two-sided quote is the observation. A one-way quote is
    skipped and counted — using its single side would report a bid as
    where the instrument trades.
    """
    observations: list[PriceObservation] = []
    one_way = 0
    for quote in book.quotes:
        if quote.bid is None or quote.ask is None:
            one_way += 1
            continue
        kind = _KIND_FOR_FIRMNESS.get(quote.firmness)
        if kind is None:
            one_way += 1
            continue
        sizes = [s for s in (quote.bid_size, quote.ask_size) if s is not None and s > 0.0]
        observations.append(
            PriceObservation(
                price=(quote.bid + quote.ask) / 2.0,
                kind=kind,
                # The contributor, not "contributed": §15 corroborates by
                # source, and two anonymous observations might be one
                # source counted twice.
                source=quote.contributor,
                observed_at=quote.quoted_at.date(),
                size=min(sizes) if sizes else None,
            )
        )
    return EvaluationInputs(observations=tuple(observations), one_way_skipped=one_way)


def evaluate_contributed(
    book: Book, *, as_of: datetime, weighting: Weighting | None = None
) -> TvalPrice:
    """The evaluated price for a contributed book, or refuse with a reason.

    Refuses rather than returning a price of `None`. An evaluated price
    that renders blank is indistinguishable from one the screen failed to
    load, and §15's whole claim is that the number says what it rests on.
    """
    inputs = observations_from_book(book, as_of=as_of)
    if not inputs.observations:
        raise UnpriceableError(
            f"{book.subject}: {len(book.quotes)} contributed quote(s), none two-sided "
            f"({inputs.one_way_skipped} one-way). A one-way market is information about "
            "direction, not about level, and pricing off a single side would report a "
            "bid as where the instrument trades"
        )
    priced: TvalPrice = evaluate_price.__wrapped__(  # type: ignore[attr-defined]
        inputs.observations,
        as_of=as_of.date(),
        weighting=weighting,
    )
    return priced


def evaluated_subjects(books: dict[TUID, Book]) -> tuple[TUID, ...]:
    """Subjects whose books can be evaluated, newest-quoted first."""
    return tuple(
        subject
        for subject, book in sorted(books.items(), key=lambda kv: str(kv[0]))
        if any(q.bid is not None and q.ask is not None for q in book.quotes)
    )


__all__ = [
    "EvaluationInputs",
    "evaluate_contributed",
    "evaluated_subjects",
    "observations_from_book",
]
