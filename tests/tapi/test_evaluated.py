"""Contributed quotes become an evaluated price (spec §15.1-15.3).

The two ends of this pipeline shipped separately and were never joined:
`tapi/contribution.py` accepted quotes, `analytics/tval/evaluate.py`
scored observations, and nothing in `treble/` imported the second. The
unread-member gate found it by flagging `WeightedObservation.age_days` as
having no reader — the field was the symptom, an unreachable Prong 1 the
cause.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from treble.analytics.tval.evaluate import ObservationKind, UnpriceableError
from treble.core.identifiers import TUID
from treble.plant.quotes import Book, Firmness, Quote
from treble.tapi.evaluated import (
    evaluate_contributed,
    evaluated_subjects,
    observations_from_book,
)

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
SUBJECT = TUID("isin:US0378331005")


def _quote(
    contributor: str,
    *,
    bid: float | None = 99.0,
    ask: float | None = 101.0,
    firmness: Firmness = Firmness.EXECUTABLE,
    size: float | None = 1_000_000.0,
) -> Quote:
    return Quote(
        subject=SUBJECT,
        contributor=contributor,
        firmness=firmness,
        bid=bid,
        ask=ask,
        bid_size=size,
        ask_size=size,
        quoted_at=NOW,
    )


def _book(*quotes: Quote) -> Book:
    return Book(subject=SUBJECT, quotes=quotes)


class TestOneWayQuotesAreNotPrices:
    def test_a_one_way_quote_produces_no_observation(self) -> None:
        """A contributor showing only a bid is making a one-way market --
        information about direction, not about level. Using the single side
        would report a bid as where the instrument trades."""
        inputs = observations_from_book(_book(_quote("A", ask=None)), as_of=NOW)
        assert inputs.observations == ()
        assert inputs.one_way_skipped == 1

    def test_the_skipped_count_travels(self) -> None:
        """Ten quotes producing two observations is a different statement
        about liquidity from ten producing ten."""
        inputs = observations_from_book(
            _book(_quote("A"), _quote("B", bid=None), _quote("C", ask=None)), as_of=NOW
        )
        assert len(inputs.observations) == 1
        assert inputs.one_way_skipped == 2

    def test_a_book_of_one_way_quotes_refuses_with_the_reason(self) -> None:
        """Refuses rather than returning a blank price: a price that renders
        empty is indistinguishable from a screen that failed to load."""
        with pytest.raises(UnpriceableError, match="one-way"):
            evaluate_contributed(_book(_quote("A", ask=None)), as_of=NOW)


class TestFirmnessSurvivesTheCrossing:
    def test_executable_and_indicative_become_different_kinds(self) -> None:
        """§15.1 weights an executable quote above an indicative one because
        they are different evidence. Collapsing them would let a stack of
        indications outvote a firm price -- the failure ALLQ's composite
        split exists to prevent, one layer up."""
        inputs = observations_from_book(
            _book(_quote("A"), _quote("B", firmness=Firmness.INDICATIVE)), as_of=NOW
        )
        kinds = {o.kind for o in inputs.observations}
        assert kinds == {ObservationKind.EXECUTABLE_QUOTE, ObservationKind.INDICATIVE_QUOTE}

    def test_the_contributor_is_the_source_not_a_label(self) -> None:
        """§15 corroborates by source. Two anonymous observations might be
        one source counted twice."""
        inputs = observations_from_book(_book(_quote("BANK-A"), _quote("BANK-B")), as_of=NOW)
        assert {o.source for o in inputs.observations} == {"BANK-A", "BANK-B"}


class TestTheEvaluatedPrice:
    def test_a_two_sided_book_prices_at_the_mid(self) -> None:
        priced = evaluate_contributed(_book(_quote("A")), as_of=NOW)
        assert priced.price == pytest.approx(100.0)

    def test_every_observation_reaches_the_drill_down_with_its_age(self) -> None:
        """The field the unread-member gate flagged. A price backed by
        300-day-old observations is a different claim from one backed by
        today's, and §15 says the drill-down states what it rests on."""
        priced = evaluate_contributed(_book(_quote("A"), _quote("B")), as_of=NOW)
        assert len(priced.observations) == 2
        assert all(o.age_days == 0 for o in priced.observations)
        assert sum(o.contribution for o in priced.observations) == pytest.approx(1.0)

    def test_size_carries_the_weaker_side(self) -> None:
        """The smaller of the two sides. A quote firm in 1mm one way and
        100k the other is 100k of evidence, not 1mm."""
        quote = _quote("A")
        thin = quote.model_copy(update={"ask_size": 100_000.0})
        inputs = observations_from_book(_book(thin), as_of=NOW)
        assert inputs.observations[0].size == pytest.approx(100_000.0)


class TestSubjectSelection:
    def test_only_evaluable_books_are_listed(self) -> None:
        books = {
            SUBJECT: _book(_quote("A")),
            TUID("isin:US9999999999"): _book(_quote("B", ask=None)),
        }
        assert evaluated_subjects(books) == (SUBJECT,)
