"""Venue adapters — a real feed into the ticker plant (spec §3.1, §8.1).

The plant's machinery — conflation, current-state image, sequence-gap
detection, bounded TPIPE — shipped tested against synthetic ticks. This is
what puts a real venue behind it.

**This criterion was recorded as blocked on data, and it was not.** The note
said venue adapters need live trade feeds that no free source provides. That
is true of equities and of every consolidated tape, and it is not true in
general: crypto exchanges publish their own matches over an unauthenticated
WebSocket. Coinbase's feed is the venue's own prints, needs no key, and is
already used by this repository for daily candles.

**Which field is the sequence, measured rather than assumed.** Coinbase
sends both `sequence` and `trade_id` on every match. `sequence` is the
*channel* sequence and counts every message on the product's book, so
consecutive trades are hundreds apart — sampled live on 2026-08-06, BTC-USD
matches ran 133829171610, 133829172196, 133829172676. Feeding it to the
plant would raise `GapDetectedError` on every single tick. `trade_id` is
per-product and contiguous across trades: 1067577991, 1067577992,
1067577993. That is the sequence the plant needs, and getting it wrong is
the difference between a gap detector that works and one that screams.

**What this is not.** One venue's crypto prints. There is no consolidated
tape in crypto, so this is Coinbase's view and the subject says so — the
same reason the daily adapter names the exchange in its subject. No equity
or bond venue is reachable this way, and none is claimed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Any, Final

from treble.ingest.coinbase import crypto_subject
from treble.plant.conflation import Tick

#: Coinbase's public market-data socket. No key: this is the same public
#: surface the daily candle adapter reads, and the same terms apply.
COINBASE_WS_URL: Final = "wss://ws-feed.exchange.coinbase.com"

#: The channel carrying executed trades. `matches` is trades only; `ticker`
#: would give a best-bid/offer summary, which is a different thing and not
#: what a tape is.
MATCH_CHANNEL: Final = "matches"

#: Message types that carry a trade. `last_match` arrives once per product on
#: subscribe and is a real, already-executed trade — it seeds the image and
#: the sequence rather than being discarded, so the first genuine `match`
#: does not look like a gap from zero.
TRADE_TYPES: Final[frozenset[str]] = frozenset({"match", "last_match"})

#: The field a trade price is published under, matching the daily series so
#: one instrument does not carry two names for its price.
PRICE_FIELD: Final = "PX_LAST"


def subscribe_message(products: Sequence[str]) -> str:
    """The subscribe frame for a set of products."""
    if not products:
        raise ValueError("subscribing to no products would open a socket that says nothing")
    return json.dumps(
        {
            "type": "subscribe",
            "product_ids": [p.upper() for p in products],
            "channels": [MATCH_CHANNEL],
        }
    )


def parse_match(message: dict[str, Any]) -> Tick | None:
    """One Coinbase match message as a plant tick, or None if it is not a trade.

    Returns None rather than raising for non-trade frames: a socket carries
    subscription acknowledgements and heartbeats, and treating those as
    malformed trades would make normal operation look like a fault. A
    malformed *trade* does raise — that is a real defect in the feed or the
    parser, and dropping it silently would lose a print.
    """
    kind = message.get("type")
    if kind not in TRADE_TYPES:
        return None

    missing = [key for key in ("product_id", "price", "trade_id", "time") if key not in message]
    if missing:
        raise ValueError(
            f"a {kind} message without {', '.join(missing)} cannot be a tick; "
            "dropping it silently would lose a print the venue reported"
        )

    price = float(message["price"])
    if price <= 0:
        raise ValueError(
            f"a trade at {price} is not a price; the venue reported {message['price']!r}"
        )

    # `trade_id`, not `sequence` — see the module docstring. This is the one
    # choice in this file that a test cannot catch by inspection, because
    # both fields are integers that increase.
    return Tick(
        subject=crypto_subject(str(message["product_id"])),
        field=PRICE_FIELD,
        value=price,
        sequence=int(message["trade_id"]),
        exchange_time=_venue_time(str(message["time"])),
    )


def _venue_time(raw: str) -> datetime:
    """The venue's own timestamp, not the moment we received it.

    Receipt time would fold this process's scheduling and the network into
    what looks like exchange data, and two subscribers would then disagree
    about when a trade happened.
    """
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def ticks_from_messages(messages: Iterator[str] | Sequence[str]) -> Iterator[Tick]:
    """Normalise a stream of raw frames into ticks, skipping non-trades.

    Separated from the socket so the whole normalisation path is testable
    against recorded frames. CI never opens a connection (CLAUDE.md §7), and
    a venue adapter whose parsing could only be exercised live would be
    untested in exactly the place that matters.
    """
    for raw in messages:
        tick = parse_match(json.loads(raw))
        if tick is not None:
            yield tick


async def stream_ticks(
    products: Sequence[str], *, limit: int | None = None, url: str = COINBASE_WS_URL
) -> list[Tick]:  # pragma: no cover - live network, exercised out of band
    """Connect, subscribe and collect ticks until `limit` is reached.

    Not covered by tests and deliberately so: the parsing is covered by
    `ticks_from_messages` against recorded frames, and what remains here is
    the socket itself. A test that opened a real connection would be a test
    that fails when the venue is quiet.
    """
    import websockets

    collected: list[Tick] = []
    async with websockets.connect(url, open_timeout=20) as socket:
        await socket.send(subscribe_message(products))
        while limit is None or len(collected) < limit:
            tick = parse_match(json.loads(await socket.recv()))
            if tick is not None:
                collected.append(tick)
    return collected


__all__ = [
    "COINBASE_WS_URL",
    "MATCH_CHANNEL",
    "PRICE_FIELD",
    "TRADE_TYPES",
    "parse_match",
    "stream_ticks",
    "subscribe_message",
    "ticks_from_messages",
]
