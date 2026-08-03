"""The contribution API — the only write path in TAPI (spec §2.2, §8.3).

Everything else in this system reads public filings. This is where a human
asserts a price, so the tests are mostly about what is refused: a bad
contribution does not fail loudly on its own, it just becomes a level on
every reader's screen with a contributor's name beside it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from treble.plant.quotes import Firmness
from treble.tapi.contribution import (
    ContributionRejectedError,
    ContributionRequest,
    ContributionService,
)

NOW = datetime(2026, 8, 2, 15, 0, tzinfo=UTC)
BOND = "cusip:912810UT3"


def request(**overrides: object) -> ContributionRequest:
    fields: dict[str, object] = {
        "subject": BOND,
        "contributor": "Dealer A",
        "firmness": Firmness.EXECUTABLE,
        "bid": 98.25,
        "ask": 98.75,
    }
    fields.update(overrides)
    return ContributionRequest(**fields)  # type: ignore[arg-type]


class TestAcceptance:
    def test_a_good_quote_reaches_the_book(self) -> None:
        service = ContributionService()
        service.contribute(request(), received_at=NOW)
        book = service.book(BOND, as_of=NOW)
        assert [q.contributor for q in book.quotes] == ["Dealer A"]
        assert book.tcmp == (98.25, 98.75)

    def test_a_contributor_replaces_its_own_quote(self) -> None:
        service = ContributionService()
        service.contribute(request(), received_at=NOW)
        service.contribute(
            request(bid=98.30, ask=98.70, quoted_at=NOW + timedelta(seconds=30)),
            received_at=NOW + timedelta(seconds=30),
        )
        book = service.book(BOND, as_of=NOW + timedelta(seconds=30))
        assert len(book.quotes) == 1
        assert book.tcmp == (98.30, 98.70)

    def test_arrival_time_is_stamped_when_none_is_given(self) -> None:
        """A contributor that does not say when the level was struck gets
        the arrival time, not a guess at the strike time. Expiry then runs
        from a moment the server can vouch for."""
        service = ContributionService()
        quote = service.contribute(request(quoted_at=None), received_at=NOW)
        assert quote.quoted_at == NOW

    def test_a_withdrawn_quote_leaves_the_book(self) -> None:
        service = ContributionService()
        service.contribute(request(), received_at=NOW)
        service.withdraw(BOND, "Dealer A")
        assert service.book(BOND, as_of=NOW).is_empty


class TestRefusals:
    """Each of these would otherwise become a level on a real screen."""

    def test_an_anonymous_contribution_is_refused(self) -> None:
        """`ALLQ` attributes every level, and attribution is what a
        contributor is paid in (§2.2). Storing it under a blank name would
        put an unattributable price on every reader's screen."""
        with pytest.raises(ContributionRejectedError, match="needs a contributor"):
            ContributionService().contribute(request(contributor="   "), received_at=NOW)

    def test_a_quote_with_no_side_is_refused(self) -> None:
        with pytest.raises(ContributionRejectedError, match="says nothing"):
            ContributionService().contribute(request(bid=None, ask=None), received_at=NOW)

    def test_a_crossed_quote_is_refused(self) -> None:
        """A bid above the offer is a data error every time, and accepting
        it would let one contributor's typo set the composite for every
        reader."""
        with pytest.raises(ContributionRejectedError, match="crossed quote"):
            ContributionService().contribute(request(bid=101.0, ask=99.0), received_at=NOW)

    def test_a_touching_market_is_accepted(self) -> None:
        """Bid equal to ask is a locked market — unusual, real, and not an
        error. The refusal above must not catch it."""
        service = ContributionService()
        service.contribute(request(bid=98.5, ask=98.5), received_at=NOW)
        assert service.book(BOND, as_of=NOW).spread == 0.0

    def test_a_non_positive_price_is_refused(self) -> None:
        with pytest.raises(ContributionRejectedError, match="not a price"):
            ContributionService().contribute(request(bid=0.0), received_at=NOW)

    def test_a_zero_size_is_refused_rather_than_stored(self) -> None:
        """Omitting size and quoting size zero are different claims. Storing
        zero would render as a real size of nothing."""
        with pytest.raises(ContributionRejectedError, match="not a size"):
            ContributionService().contribute(request(bid_size=0.0), received_at=NOW)

    def test_a_quote_from_the_future_is_refused(self) -> None:
        """Expiry is measured from `quoted_at`, so a future timestamp is a
        quote that never goes stale — it would sit on the screen for ever,
        looking live."""
        with pytest.raises(ContributionRejectedError, match="never goes stale"):
            ContributionService().contribute(
                request(quoted_at=NOW + timedelta(hours=1)), received_at=NOW
            )

    def test_a_naive_timestamp_is_refused(self) -> None:
        with pytest.raises(ContributionRejectedError, match="timezone-aware"):
            ContributionService().contribute(
                request(quoted_at=datetime(2026, 8, 2, 15, 0)),  # noqa: DTZ001
                received_at=NOW,
            )

    def test_firmness_cannot_be_omitted(self) -> None:
        """Not a service check — the model refuses it, so no code path can
        construct a contribution that does not say whether it is firm."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ContributionRequest(subject=BOND, contributor="A", bid=98.0)  # type: ignore[call-arg]


class TestExpiry:
    def test_a_stale_quote_leaves_the_book_empty_not_stale(self) -> None:
        service = ContributionService(ttl=timedelta(minutes=5))
        service.contribute(request(), received_at=NOW)
        later = NOW + timedelta(minutes=6)
        book = service.book(BOND, as_of=later)
        assert book.is_empty
        assert book.tcmp == (None, None)

    def test_the_book_remembers_when_it_was_last_live(self) -> None:
        """An empty screen that cannot say how long it has been empty is
        indistinguishable from one that failed to load."""
        service = ContributionService(ttl=timedelta(minutes=5))
        service.contribute(request(), received_at=NOW)
        book = service.book(BOND, as_of=NOW + timedelta(minutes=6))
        assert book.last_live == NOW

    def test_an_instrument_never_quoted_says_never(self) -> None:
        assert ContributionService().book(BOND, as_of=NOW).last_live is None


class TestCorrectWhenEmpty:
    def test_an_unquoted_instrument_returns_a_book(self) -> None:
        """The Phase 2 criterion. A book, not None: an empty book is a fact
        about the market, and returning nothing makes it indistinguishable
        from a screen that failed to load."""
        book = ContributionService().book(BOND, as_of=NOW)
        assert book.is_empty
        assert book.quotes == ()
        assert book.best_bid is None and book.best_ask is None
        assert book.spread is None
        assert book.tcmp == (None, None)
        assert book.tgn == (None, None)
