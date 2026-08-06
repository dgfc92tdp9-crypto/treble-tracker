"""The Coinbase venue adapter feeding the ticker plant (spec §3.1, §8.1).

Every test here runs against 40 frames recorded live from
`wss://ws-feed.exchange.coinbase.com` on 2026-08-06. CI never opens a socket
(CLAUDE.md §7), and a venue adapter whose parsing could only be exercised
against a live feed would be untested in exactly the place that matters —
and would fail whenever the venue was quiet.

The test that carries the most weight is
`test_trade_id_is_the_sequence_not_the_channel_sequence`. Both fields are
integers that increase, so nothing about the code's *appearance* says which
is right, and choosing wrong makes the plant raise a gap on every tick.
"""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

import pytest

from treble.plant.conflation import GapDetectedError, TickerPlant
from treble.plant.venues import (
    MATCH_CHANNEL,
    PRICE_FIELD,
    parse_match,
    subscribe_message,
    ticks_from_messages,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "coinbase" / "ws_matches.jsonl"


def frames() -> list[str]:
    return FIXTURE.read_text().splitlines()


def messages() -> list[dict[str, object]]:
    return [json.loads(line) for line in frames()]


class TestTheRecordedFeedIsWhatWeThinkItIs:
    def test_the_fixture_holds_real_trades(self) -> None:
        """Guards every test below: a fixture that had lost its match frames
        would let the parser tests pass with nothing to parse."""
        kinds = [m.get("type") for m in messages()]
        assert kinds.count("match") > 20
        assert "subscriptions" in kinds

    def test_trade_id_is_the_sequence_not_the_channel_sequence(self) -> None:
        """The measurement the adapter rests on.

        `sequence` counts every message on the product's book, so consecutive
        trades are hundreds apart. `trade_id` is contiguous across trades.
        Both are increasing integers, so only this distinguishes them — and
        feeding `sequence` to the plant raises a gap on every tick.
        """
        for product in ("BTC-USD", "ETH-USD"):
            trades = [
                m
                for m in messages()
                if m.get("type") in ("match", "last_match") and m.get("product_id") == product
            ]
            if len(trades) < 3:
                continue
            ids = [int(str(m["trade_id"])) for m in trades]
            channel = [int(str(m["sequence"])) for m in trades]
            gaps = [b - a for a, b in pairwise(ids)]
            assert set(gaps) == {1}, f"{product}: trade_id was not contiguous: {gaps}"
            channel_gaps = [b - a for a, b in pairwise(channel)]
            assert max(channel_gaps) > 1, (
                f"{product}: the channel sequence was contiguous in this sample, so this test "
                "no longer demonstrates the distinction it exists for"
            )


class TestParsing:
    def test_a_match_becomes_a_tick(self) -> None:
        trade = next(m for m in messages() if m.get("type") == "match")
        tick = parse_match(trade)
        assert tick is not None
        assert tick.field == PRICE_FIELD
        assert tick.value == pytest.approx(float(str(trade["price"])))
        assert tick.sequence == int(str(trade["trade_id"]))

    def test_the_subject_names_the_venue(self) -> None:
        """Crypto has no consolidated tape, so a price is one venue's. A
        subject that did not say which would make two exchanges' prints look
        like disagreement about one number."""
        tick = parse_match(next(m for m in messages() if m.get("type") == "match"))
        assert tick is not None
        assert "coinbase" in str(tick.subject)

    def test_the_venue_timestamp_is_used_not_receipt_time(self) -> None:
        """Receipt time folds this process's scheduling and the network into
        what looks like exchange data."""
        trade = next(m for m in messages() if m.get("type") == "match")
        tick = parse_match(trade)
        assert tick is not None
        assert tick.exchange_time.isoformat().startswith(str(trade["time"])[:19])
        assert tick.exchange_time.tzinfo is not None

    def test_non_trade_frames_are_skipped_not_rejected(self) -> None:
        """A socket carries subscription acknowledgements and heartbeats.
        Treating those as malformed trades would make normal operation look
        like a fault."""
        acknowledgement = next(m for m in messages() if m.get("type") == "subscriptions")
        assert parse_match(acknowledgement) is None

    def test_a_malformed_trade_raises_rather_than_being_dropped(self) -> None:
        """The opposite case, and the reason the one above is not a blanket
        `return None`: a match the parser cannot read is a lost print."""
        with pytest.raises(ValueError, match="cannot be a tick"):
            parse_match({"type": "match", "product_id": "BTC-USD", "price": "1"})

    def test_a_non_positive_price_is_refused(self) -> None:
        with pytest.raises(ValueError, match="is not a price"):
            parse_match(
                {
                    "type": "match",
                    "product_id": "BTC-USD",
                    "price": "0",
                    "trade_id": 1,
                    "time": "2026-08-06T12:00:00.000000Z",
                }
            )

    def test_last_match_seeds_rather_than_being_discarded(self) -> None:
        """`last_match` arrives once per product on subscribe and is a real,
        already-executed trade. Discarding it would leave the plant with no
        sequence for that product, so the first genuine match would look
        like a gap from zero."""
        seed = next(m for m in messages() if m.get("type") == "last_match")
        assert parse_match(seed) is not None


class TestItFeedsThePlant:
    def test_the_recorded_stream_publishes_without_a_gap(self) -> None:
        """The end-to-end claim: real frames, in the order the venue sent
        them, through the real plant. If `sequence` were used instead of
        `trade_id` this raises on the second tick of each product."""
        plant = TickerPlant()
        published = 0
        for tick in ticks_from_messages(frames()):
            plant.publish(tick)
            published += 1
        assert published > 20
        assert plant.instruments >= 1

    def test_the_image_holds_each_products_latest_price(self) -> None:
        plant = TickerPlant()
        ticks = list(ticks_from_messages(frames()))
        for tick in ticks:
            plant.publish(tick)
        for subject in {t.subject for t in ticks}:
            latest = max(t.sequence for t in ticks if t.subject == subject)
            image = plant.image(subject, PRICE_FIELD)
            assert image is not None
            assert image.sequence == latest

    def test_using_the_channel_sequence_would_be_caught(self) -> None:
        """Proof that the plant's gap detector would have caught the wrong
        choice — so this adapter's correctness rests on a mechanism that
        demonstrably fires, not on the parser having looked right."""
        plant = TickerPlant()
        trades = [
            m
            for m in messages()
            if m.get("type") in ("match", "last_match") and m.get("product_id") == "BTC-USD"
        ]
        if len(trades) < 3:
            pytest.skip("not enough BTC-USD trades recorded to demonstrate the gap")
        with pytest.raises(GapDetectedError):
            for message in trades:
                tick = parse_match(message)
                assert tick is not None
                plant.publish(tick.model_copy(update={"sequence": int(str(message["sequence"]))}))


class TestSubscription:
    def test_the_subscribe_frame_asks_for_trades(self) -> None:
        """`matches` is trades; `ticker` would be a best-bid/offer summary,
        which is a different thing and not a tape."""
        frame = json.loads(subscribe_message(["btc-usd"]))
        assert frame["channels"] == [MATCH_CHANNEL]
        assert frame["product_ids"] == ["BTC-USD"]

    def test_subscribing_to_nothing_is_refused(self) -> None:
        with pytest.raises(ValueError, match="says nothing"):
            subscribe_message([])


def test_the_plant_subject_matches_the_daily_series() -> None:
    """One instrument, one name. If the tick feed and the daily candle
    adapter disagreed on the subject, `GP` would draw a history that the
    live price never joined."""
    from treble.ingest.coinbase import crypto_subject

    trade = next(m for m in messages() if m.get("type") == "match")
    tick = parse_match(trade)
    assert tick is not None
    assert tick.subject == crypto_subject(str(trade["product_id"]))
