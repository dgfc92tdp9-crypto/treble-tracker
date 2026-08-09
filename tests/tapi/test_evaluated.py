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
from treble.core.identifiers import TUID, SecurityQuery, YellowKey
from treble.plant.quotes import Book, Firmness, Quote
from treble.tapi.evaluated import (
    evaluate_contributed,
    evaluated_subjects,
    observations_from_book,
)
from treble.tapi.local import LocalTapi, TickerIndex

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


class TestTheAllqBinding:
    """`sys:allq_evaluated`. ALLQ shows the quotes; this shows what they add
    up to, from the same book at the same moment, so a user sees the
    evidence and the conclusion without wondering whether they were computed
    from the same thing."""

    @staticmethod
    def _tapi(requests: tuple[tuple[str, float | None, float | None], ...]) -> LocalTapi:
        import tempfile
        from pathlib import Path

        from treble.store.duck import DuckStore
        from treble.tapi.contribution import ContributionRequest, ContributionService

        service = ContributionService()
        for contributor, bid, ask in requests:
            service.contribute(
                ContributionRequest(
                    subject="cik:0000051143",
                    contributor=contributor,
                    firmness=Firmness.EXECUTABLE,
                    bid=bid,
                    ask=ask,
                    bid_size=1_000_000.0,
                    ask_size=1_000_000.0,
                ),
                received_at=NOW,
            )
        store = DuckStore(Path(tempfile.mkdtemp()) / "t.db")
        return LocalTapi(store, tickers=TickerIndex({"IBM": 51143}), contributions=service)

    @staticmethod
    def _query() -> SecurityQuery:
        return SecurityQuery(ticker="IBM", key=YellowKey.EQUITY, venue=None, descriptor=None)

    def test_an_empty_book_says_so_rather_than_failing(self) -> None:
        """ALLQ's correct-when-empty case, restated: an empty network and a
        screen that failed to load must not render alike."""
        rows = self._tapi(()).series(self._query(), "sys:allq_evaluated", as_of=NOW)
        assert "No contributed quotes" in str(rows[0][0])

    def test_the_one_way_count_shows_even_when_the_price_succeeds(self) -> None:
        """A book that produced two observations is a different statement
        about liquidity from one that produced ten, and a price that did not
        say so would look equally well-supported either way."""
        tapi = self._tapi((("A", 99.0, 101.0), ("B", 99.0, None)))
        rows = tapi.series(self._query(), "sys:allq_evaluated", as_of=NOW)
        flat = {r[0]: r[1] for r in rows}
        assert flat["ONE-WAY QUOTES SKIPPED"] == 1
        assert flat["OBSERVATIONS"] == 1
        assert flat["EVALUATED PRICE"] == pytest.approx(100.0)

    def test_the_level_and_score_travel_with_the_price(self) -> None:
        """§15's whole claim is a price *and* how much to trust it."""
        tapi = self._tapi((("A", 99.0, 101.0), ("B", 99.5, 100.5)))
        flat = {r[0]: r[1] for r in tapi.series(self._query(), "sys:allq_evaluated", as_of=NOW)}
        assert flat["FAIR VALUE LEVEL"]
        assert 1 <= int(flat["SCORE (1-10)"]) <= 10
        assert flat["DROPPED AS STALE"] == 0
