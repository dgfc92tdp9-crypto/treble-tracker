"""`ALLQ` correct-when-empty (Phase 2 gate criterion).

Most of these are about the empty book, because that is the case the
criterion names and the one where every wrong answer looks reasonable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from treble.plant.quotes import Firmness, Quote, QuoteBook

T0 = datetime(2026, 8, 1, 14, 30, tzinfo=UTC)
IBM = "equity:IBM"


def quote(
    contributor: str,
    bid: float | None,
    ask: float | None,
    at: datetime = T0,
    firmness: Firmness = Firmness.EXECUTABLE,
) -> Quote:
    return Quote(
        subject=IBM,
        contributor=contributor,
        firmness=firmness,
        bid=bid,
        ask=ask,
        quoted_at=at,
    )


class TestCorrectWhenEmpty:
    def test_an_unquoted_instrument_returns_a_book_not_nothing(self) -> None:
        """An empty book is a fact about the market. Returning None makes it
        indistinguishable from a screen that failed to load."""
        book = QuoteBook().book(IBM, as_of=T0)
        assert book.is_empty
        assert book.quotes == ()

    def test_an_empty_book_has_no_best_prices(self) -> None:
        """Not zero. A bid of nothing is not a bid."""
        book = QuoteBook().book(IBM, as_of=T0)
        assert book.best_bid is None
        assert book.best_ask is None

    def test_an_empty_book_has_no_spread(self) -> None:
        assert QuoteBook().book(IBM, as_of=T0).spread is None

    def test_expired_quotes_leave_the_book_empty_not_stale(self) -> None:
        """The tempting wrong answer: showing the last quote received, which
        nobody will honour any more, as though it were live."""
        b = QuoteBook(ttl=timedelta(minutes=5))
        b.contribute(quote("dealerA", 100.0, 100.5))
        assert b.book(IBM, as_of=T0 + timedelta(minutes=6)).is_empty

    def test_an_empty_book_says_when_it_last_was_not(self) -> None:
        """The difference between "nobody is quoting" and "this screen is
        broken" is exactly this field."""
        b = QuoteBook(ttl=timedelta(minutes=5))
        b.contribute(quote("dealerA", 100.0, 100.5))
        assert b.book(IBM, as_of=T0 + timedelta(minutes=6)).last_live == T0

    def test_a_never_quoted_instrument_has_no_last_live(self) -> None:
        """Nothing to remember, and no date invented to fill the space."""
        assert QuoteBook().book(IBM, as_of=T0).last_live is None

    def test_a_live_book_does_not_carry_last_live(self) -> None:
        """On a live book the quotes themselves say how current it is."""
        b = QuoteBook()
        b.contribute(quote("dealerA", 100.0, 100.5))
        assert b.book(IBM, as_of=T0).last_live is None


class TestBestPrices:
    def test_best_bid_is_the_highest_and_best_ask_the_lowest(self) -> None:
        b = QuoteBook()
        b.contribute(quote("dealerA", 100.0, 100.6))
        b.contribute(quote("dealerB", 100.2, 100.5))
        book = b.book(IBM, as_of=T0)
        assert (book.best_bid, book.best_ask) == (100.2, 100.5)
        assert book.spread == pytest_approx(0.3)

    def test_a_one_way_market_has_no_spread(self) -> None:
        """Returning zero, or a distance to a remembered other side, would
        invent a market that is not there."""
        b = QuoteBook()
        b.contribute(quote("dealerA", 100.0, None))
        book = b.book(IBM, as_of=T0)
        assert book.best_bid == 100.0
        assert book.best_ask is None
        assert book.spread is None

    def test_a_one_way_contributor_still_appears(self) -> None:
        """A one-way market is information, not a malformed quote."""
        b = QuoteBook()
        b.contribute(quote("dealerA", 100.0, None))
        assert len(b.book(IBM, as_of=T0).quotes) == 1


class TestContributorLifecycle:
    def test_a_contributor_replaces_its_own_quote(self) -> None:
        b = QuoteBook()
        b.contribute(quote("dealerA", 100.0, 100.5))
        b.contribute(quote("dealerA", 101.0, 101.5, at=T0 + timedelta(seconds=1)))
        book = b.book(IBM, as_of=T0 + timedelta(seconds=1))
        assert len(book.quotes) == 1
        assert book.best_bid == 101.0

    def test_an_out_of_order_quote_does_not_revert_a_price(self) -> None:
        b = QuoteBook()
        b.contribute(quote("dealerA", 101.0, 101.5, at=T0 + timedelta(seconds=1)))
        b.contribute(quote("dealerA", 100.0, 100.5, at=T0))
        assert b.book(IBM, as_of=T0 + timedelta(seconds=1)).best_bid == 101.0

    def test_withdrawal_removes_rather_than_zeroes(self) -> None:
        """A withdrawn quote is absent, not a price of nothing."""
        b = QuoteBook()
        b.contribute(quote("dealerA", 100.0, 100.5))
        b.withdraw(IBM, "dealerA")
        assert b.book(IBM, as_of=T0).is_empty

    def test_expiry_is_per_contributor_not_per_book(self) -> None:
        """A book expiring wholesale would discard a live market maker
        alongside a stale one."""
        b = QuoteBook(ttl=timedelta(minutes=5))
        b.contribute(quote("stale", 99.0, 99.5, at=T0))
        b.contribute(quote("live", 100.0, 100.5, at=T0 + timedelta(minutes=4)))
        book = b.book(IBM, as_of=T0 + timedelta(minutes=6))
        assert [q.contributor for q in book.quotes] == ["live"]

    def test_quotes_are_ordered_deterministically(self) -> None:
        b = QuoteBook()
        for name in ("zeta", "alpha", "mid"):
            b.contribute(quote(name, 100.0, 100.5))
        assert [q.contributor for q in b.book(IBM, as_of=T0).quotes] == ["alpha", "mid", "zeta"]


def pytest_approx(value: float) -> object:
    import pytest

    return pytest.approx(value)


class TestFirmness:
    """Indicative and executable are different claims (spec §2.2)."""

    def test_firmness_must_be_stated(self) -> None:
        """No default. Defaulting to executable presents an opinion as a
        commitment; defaulting to indicative throws away the one thing that
        makes a quote actionable. Either is wrong half the time, so the
        contributor has to say."""
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Quote(subject=IBM, contributor="A", bid=99.0, quoted_at=T0)  # type: ignore[call-arg]

    def test_the_executable_composite_ignores_indicative_levels(self) -> None:
        """`TCMP` is the composite *executable* price. Letting an indicative
        level set it would produce the most dangerous number on the screen —
        one that reads as tradeable and is not."""
        book = QuoteBook()
        book.contribute(quote("firm", 99.0, 101.0, firmness=Firmness.EXECUTABLE))
        book.contribute(quote("soft", 99.9, 100.1, firmness=Firmness.INDICATIVE))
        rendered = book.book(IBM, as_of=T0)
        assert rendered.tcmp == (99.0, 101.0)

    def test_the_indicative_composite_uses_every_live_quote(self) -> None:
        book = QuoteBook()
        book.contribute(quote("firm", 99.0, 101.0, firmness=Firmness.EXECUTABLE))
        book.contribute(quote("soft", 99.9, 100.1, firmness=Firmness.INDICATIVE))
        assert book.book(IBM, as_of=T0).tgn == (99.9, 100.1)

    def test_the_two_composites_are_not_the_same_property(self) -> None:
        """When a tighter indicative level exists they must differ, or the
        distinction has been lost somewhere between the book and the
        screen."""
        book = QuoteBook()
        book.contribute(quote("firm", 99.0, 101.0, firmness=Firmness.EXECUTABLE))
        book.contribute(quote("soft", 99.9, 100.1, firmness=Firmness.INDICATIVE))
        rendered = book.book(IBM, as_of=T0)
        assert rendered.tgn != rendered.tcmp

    def test_they_agree_when_every_contributor_is_firm(self) -> None:
        """A fact about the market, not an artefact of the code."""
        book = QuoteBook()
        book.contribute(quote("a", 99.0, 101.0))
        book.contribute(quote("b", 99.5, 100.5))
        rendered = book.book(IBM, as_of=T0)
        assert rendered.tgn == rendered.tcmp == (99.5, 100.5)

    def test_an_all_indicative_book_has_no_executable_composite(self) -> None:
        """Not the indicative number under an executable label. Nobody has
        committed to anything, and the screen must be able to say so."""
        book = QuoteBook()
        book.contribute(quote("soft", 99.0, 101.0, firmness=Firmness.INDICATIVE))
        rendered = book.book(IBM, as_of=T0)
        assert rendered.tcmp == (None, None)
        assert rendered.tgn == (99.0, 101.0)

    def test_an_empty_book_has_neither_composite(self) -> None:
        book = QuoteBook().book(IBM, as_of=T0)
        assert book.tcmp == (None, None)
        assert book.tgn == (None, None)


class TestSize:
    def test_size_travels_with_the_quote(self) -> None:
        book = QuoteBook()
        book.contribute(
            Quote(
                subject=IBM,
                contributor="A",
                firmness=Firmness.EXECUTABLE,
                bid=99.0,
                ask=101.0,
                bid_size=5_000_000.0,
                ask_size=2_000_000.0,
                quoted_at=T0,
            )
        )
        held = book.book(IBM, as_of=T0).quotes[0]
        assert (held.bid_size, held.ask_size) == (5_000_000.0, 2_000_000.0)

    def test_absent_size_is_none_not_zero(self) -> None:
        """A level published without size is a weaker level, not a level in
        zero size — and the two must not render the same."""
        held = QuoteBook()
        held.contribute(quote("A", 99.0, 101.0))
        rendered = held.book(IBM, as_of=T0).quotes[0]
        assert rendered.bid_size is None
        assert rendered.ask_size is None
