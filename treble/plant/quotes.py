"""The quote book behind `ALLQ` (spec §7, Phase 2 gate).

The gate criterion is "**`ALLQ` correct-when-empty**", and the phrasing is
the requirement. Every quote screen is easy to get right when quotes are
flowing; the hard case is the instrument nobody is currently making a market
in, where the tempting answers are all wrong:

- showing the last quote received, which is no longer a quote anyone will
  honour, presented as though it were;
- showing a composite computed from expired contributions, which looks
  freshest of all because it has a timestamp of now;
- showing nothing at all, which is indistinguishable from a screen that
  failed to load.

The correct answer is a book that is *visibly* empty, and says when it last
was not. That is what this module enforces.

**Expiry is by contributor, not by book.** Contributors quote at different
frequencies, and a book that expired wholesale would discard a live market
maker alongside a stale one.
"""

from __future__ import annotations

import enum
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict

from treble.core.identifiers import TUID


class Side(enum.Enum):
    BID = "bid"
    ASK = "ask"


class Quote(BaseModel):
    """One contributor's two-sided price, with the time it was given.

    Both sides are optional: a contributor showing only a bid is making a
    one-way market, which is information, not a malformed quote.
    """

    model_config = ConfigDict(frozen=True)

    subject: TUID
    contributor: str
    bid: float | None = None
    ask: float | None = None
    #: When the contributor published it. Expiry is measured from here, not
    #: from arrival: a quote delayed in transit is already partly spent.
    quoted_at: datetime


class Book(BaseModel):
    """What `ALLQ` renders for one instrument at one moment."""

    model_config = ConfigDict(frozen=True)

    subject: TUID
    quotes: tuple[Quote, ...]
    #: When the book last held a live quote. Present only when the book is
    #: empty now — an empty screen that cannot say "and it has been empty
    #: since Friday" is indistinguishable from one that failed to load.
    last_live: datetime | None = None

    @property
    def is_empty(self) -> bool:
        return not self.quotes

    @property
    def best_bid(self) -> float | None:
        bids = [q.bid for q in self.quotes if q.bid is not None]
        return max(bids) if bids else None

    @property
    def best_ask(self) -> float | None:
        asks = [q.ask for q in self.quotes if q.ask is not None]
        return min(asks) if asks else None

    @property
    def spread(self) -> float | None:
        """None unless both sides are live.

        A one-sided market has no spread. Returning zero, or the distance to
        some remembered other side, would invent a market that is not there.
        """
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid


class QuoteBook:
    """Live contributed quotes per instrument, expiring by contributor."""

    def __init__(self, *, ttl: timedelta = timedelta(minutes=5)) -> None:
        self._quotes: dict[TUID, dict[str, Quote]] = {}
        self._last_live: dict[TUID, datetime] = {}
        self._ttl = ttl

    def contribute(self, quote: Quote) -> None:
        """Accept a contribution. A later quote replaces that contributor's
        earlier one; contributors never accumulate."""
        book = self._quotes.setdefault(quote.subject, {})
        existing = book.get(quote.contributor)
        if existing is not None and existing.quoted_at > quote.quoted_at:
            # Out of order: the newer quote already stands. Applying this
            # would revert the contributor's price to an older one.
            return
        book[quote.contributor] = quote
        if quote.bid is not None or quote.ask is not None:
            self._last_live[quote.subject] = quote.quoted_at

    def withdraw(self, subject: TUID, contributor: str) -> None:
        """A contributor pulls its market. Removed rather than zeroed: a
        withdrawn quote is absent, not a price of nothing."""
        self._quotes.get(subject, {}).pop(contributor, None)

    def book(self, subject: TUID, *, as_of: datetime) -> Book:
        """The book as of a moment, with expired contributions dropped.

        Always returns a Book, never None. An instrument nobody quotes has
        an empty book, and that is a fact about the market rather than a
        missing answer.
        """
        live = tuple(
            quote
            for quote in self._quotes.get(subject, {}).values()
            if as_of - quote.quoted_at <= self._ttl
            and (quote.bid is not None or quote.ask is not None)
        )
        return Book(
            subject=subject,
            quotes=tuple(sorted(live, key=lambda q: q.contributor)),
            # Only when empty: on a live book the newest quote already tells
            # a reader how current the market is.
            last_live=None if live else self._last_live.get(subject),
        )
