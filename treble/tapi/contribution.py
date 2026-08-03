"""The contribution API — how a quote enters the network (spec §2.2, §8.3).

    Any participant may contribute indicative or executable quotes...
    The cost of participation is zero.

This is the write side of TAPI, and the only one. Everything else in this
system reads public filings; this is where a human asserts a price. That
makes attribution and refusal the whole job:

- **Every quote is attributed.** `ALLQ` shows the contributor's name beside
  the level (§2.2 — attribution *is* the incentive), so an anonymous
  contribution is not a contribution. Rejected, not stored under "unknown".
- **A quote with no side is rejected.** A contribution with neither bid nor
  ask says nothing, and storing it would put a contributor's name on the
  screen next to two dashes as though they were making a market.
- **A crossed quote is rejected.** A bid above the offer is a data error
  every time; accepting it would let one contributor's typo set the
  composite for everyone.
- **A quote from the future is rejected.** Expiry is measured from
  `quoted_at`, so a future timestamp is a quote that never goes stale.

Quotes live in memory and expire. They are *not* facts in the bitemporal
store: a fact is something that was published and stays true of the moment
it described, while a quote is an offer that dies in minutes. Writing them
to the store would make the fact table a place where things stop being
true, which is precisely what I2 forbids.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict

from treble.core.identifiers import TUID
from treble.plant.quotes import Book, Firmness, Quote, QuoteBook


class ContributionRejectedError(ValueError):
    """The quote was not accepted, and the reason names what to fix."""


class ContributionRequest(BaseModel):
    """One contributor's two-sided price, as submitted."""

    model_config = ConfigDict(frozen=True)

    subject: str
    contributor: str
    firmness: Firmness
    bid: float | None = None
    ask: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    #: When the contributor struck the level. Optional on the wire; the
    #: server stamps arrival time when it is absent, and says so, rather
    #: than pretending to know when the price was made.
    quoted_at: datetime | None = None


class ContributionService:
    """Accepts quotes and answers `ALLQ`.

    In-process and in-memory. A contributed network needs a shared server
    to be a network, and that belongs with the gRPC/Arrow Flight transports
    (spec §8.3) rather than here — but the *semantics* are the semantics
    either way, and this is where they are enforced.
    """

    def __init__(self, *, ttl: timedelta = timedelta(minutes=5)) -> None:
        self._book = QuoteBook(ttl=ttl)
        self._ttl = ttl

    @property
    def ttl(self) -> timedelta:
        return self._ttl

    def contribute(self, request: ContributionRequest, *, received_at: datetime) -> Quote:
        """Validate and accept one contribution, or refuse with a reason."""
        if not request.contributor.strip():
            raise ContributionRejectedError(
                "a contribution needs a contributor: ALLQ attributes every level, and "
                "attribution is what a contributor is paid in (spec §2.2)"
            )
        if not str(request.subject).strip():
            raise ContributionRejectedError("a contribution needs a subject to be about")
        if request.bid is None and request.ask is None:
            raise ContributionRejectedError(
                "a quote with neither bid nor ask says nothing; storing it would put "
                f"{request.contributor!r} on screen beside two dashes as though they "
                "were making a market"
            )
        for label, price in (("bid", request.bid), ("ask", request.ask)):
            if price is not None and price <= 0.0:
                raise ContributionRejectedError(f"{label} of {price} is not a price")
        for label, size in (("bid_size", request.bid_size), ("ask_size", request.ask_size)):
            if size is not None and size <= 0.0:
                raise ContributionRejectedError(
                    f"{label} of {size} is not a size; omit it to publish a level "
                    "without size, which is a different and honest claim"
                )
        if request.bid is not None and request.ask is not None and request.bid > request.ask:
            raise ContributionRejectedError(
                f"crossed quote: bid {request.bid} is above ask {request.ask}. This is a "
                "data error every time, and accepting it would let one typo set the "
                "composite for every reader"
            )

        quoted_at = request.quoted_at or received_at
        if quoted_at.tzinfo is None:
            raise ContributionRejectedError("quoted_at must be timezone-aware")
        if quoted_at > received_at:
            raise ContributionRejectedError(
                f"quoted_at {quoted_at.isoformat()} is after arrival "
                f"{received_at.isoformat()}: expiry is measured from when the level was "
                "struck, so a future timestamp is a quote that never goes stale"
            )

        quote = Quote(
            subject=TUID(request.subject),
            contributor=request.contributor.strip(),
            firmness=request.firmness,
            bid=request.bid,
            ask=request.ask,
            bid_size=request.bid_size,
            ask_size=request.ask_size,
            quoted_at=quoted_at,
        )
        self._book.contribute(quote)
        return quote

    def withdraw(self, subject: str, contributor: str) -> None:
        """A contributor pulls its market."""
        self._book.withdraw(TUID(subject), contributor)

    def book(self, subject: str, *, as_of: datetime | None = None) -> Book:
        """The `ALLQ` book. Always a book, never None."""
        return self._book.book(TUID(subject), as_of=as_of or datetime.now(UTC))


__all__ = [
    "ContributionRejectedError",
    "ContributionRequest",
    "ContributionService",
]
