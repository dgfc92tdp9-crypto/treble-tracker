"""The conflated/unconflated split (spec §6.2, §8.2).

Both failure modes here are silent, so most of these tests are about what
must *not* happen: a display showing a superseded price, and a full-tick
stream quietly missing prints.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from treble.plant.conflation import GapDetectedError, Tick, TickerPlant

T0 = datetime(2026, 8, 1, 14, 30, tzinfo=UTC)


def tick(seq: int, value: float, subject: str = "equity:IBM", field: str = "PX_LAST") -> Tick:
    return Tick(subject=subject, field=field, value=value, sequence=seq, exchange_time=T0)


class TestConflationKeepsTheLatest:
    def test_the_display_shows_the_newest_value(self) -> None:
        """Conflation may drop what was superseded. It may never drop the
        thing that superseded it."""
        plant = TickerPlant()
        for i, price in enumerate([100.0, 101.0, 102.5], start=1):
            plant.publish(tick(i, price))
        assert plant.conflated() == (tick(3, 102.5),)

    def test_five_hundred_ticks_collapse_to_one(self) -> None:
        """The case §6.2 names: 500 updates a second, a screen at a few
        hertz. The user sees the latest, not the 37th."""
        plant = TickerPlant()
        for i in range(1, 501):
            plant.publish(tick(i, 100.0 + i))
        conflated = plant.conflated()
        assert len(conflated) == 1
        assert conflated[0].value == 600.0

    def test_each_instrument_conflates_independently(self) -> None:
        plant = TickerPlant()
        plant.publish(tick(1, 100.0, subject="equity:IBM"))
        plant.publish(tick(1, 200.0, subject="equity:AAPL"))
        plant.publish(tick(2, 101.0, subject="equity:IBM"))
        assert {t.subject: t.value for t in plant.conflated()} == {
            "equity:IBM": 101.0,
            "equity:AAPL": 200.0,
        }

    def test_fields_on_one_instrument_do_not_overwrite_each_other(self) -> None:
        """Bid and last are different facts about the same instrument."""
        plant = TickerPlant()
        plant.publish(tick(1, 100.0, field="PX_LAST"))
        plant.publish(tick(2, 99.5, field="PX_BID"))
        assert len(plant.conflated()) == 2


class TestInitialPaint:
    def test_a_new_subscriber_gets_an_image_immediately(self) -> None:
        """§8.2: an image then deltas, rather than waiting for the next
        tick — which for an illiquid instrument could be hours."""
        plant = TickerPlant()
        plant.publish(tick(1, 100.0))
        assert plant.image("equity:IBM", "PX_LAST").value == 100.0

    def test_an_unknown_instrument_has_no_image(self) -> None:
        """None, not a zero: a price of nothing is not a price."""
        assert TickerPlant().image("equity:NOPE", "PX_LAST") is None


class TestTpipeLosesNothing:
    def test_every_tick_is_delivered(self) -> None:
        plant = TickerPlant()
        for i in range(1, 101):
            plant.publish(tick(i, 100.0 + i))
        assert [t.sequence for t in plant.tpipe()] == list(range(1, 101))

    def test_arrival_order_is_preserved(self) -> None:
        plant = TickerPlant()
        plant.publish(tick(1, 100.0, subject="equity:IBM"))
        plant.publish(tick(1, 200.0, subject="equity:AAPL"))
        plant.publish(tick(2, 101.0, subject="equity:IBM"))
        assert [t.subject for t in plant.tpipe()] == [
            "equity:IBM",
            "equity:AAPL",
            "equity:IBM",
        ]

    def test_the_two_paths_agree_on_the_final_state(self) -> None:
        """Conflated and unconflated are two views of one truth. If the last
        tick on TPIPE disagreed with the display, one of them is lying."""
        plant = TickerPlant()
        for i in range(1, 51):
            plant.publish(tick(i, 100.0 + i))
        display = plant.conflated()[0]
        last_streamed = list(plant.tpipe())[-1]
        assert display == last_streamed

    def test_an_overflow_is_reported_not_hidden(self) -> None:
        """A consumer that asked for full tick and silently received most of
        it computes a VWAP from prints it does not know are missing."""
        plant = TickerPlant(tpipe_buffer=10)
        for i in range(1, 21):
            plant.publish(tick(i, 100.0 + i))
        with pytest.raises(GapDetectedError, match="overflowed"):
            list(plant.tpipe())

    def test_the_display_survives_a_tpipe_overflow(self) -> None:
        """The paths are independent: a slow machine consumer must not blank
        the screen of every human watching."""
        plant = TickerPlant(tpipe_buffer=10)
        for i in range(1, 21):
            plant.publish(tick(i, 100.0 + i))
        assert plant.conflated()[0].value == 120.0


class TestSequenceIntegrity:
    def test_an_upstream_gap_raises(self) -> None:
        """A skipped sequence means the venue or the wire lost something.
        Accepting it silently would present an incomplete record as whole."""
        plant = TickerPlant()
        plant.publish(tick(1, 100.0))
        with pytest.raises(GapDetectedError, match="sequence jumped"):
            plant.publish(tick(5, 104.0))

    def test_the_gap_says_how_much_was_lost(self) -> None:
        plant = TickerPlant()
        plant.publish(tick(1, 100.0))
        with pytest.raises(GapDetectedError, match="3 update"):
            plant.publish(tick(5, 104.0))

    def test_a_late_tick_does_not_move_the_price_backwards(self) -> None:
        """Out-of-order arrival is normal on a real wire. Applying it would
        walk the displayed price back in time, and nothing downstream would
        notice."""
        plant = TickerPlant()
        plant.publish(tick(1, 100.0))
        plant.publish(tick(2, 101.0))
        plant.publish(tick(1, 100.0))  # late duplicate
        assert plant.conflated()[0].value == 101.0

    def test_a_duplicate_is_not_streamed_twice(self) -> None:
        plant = TickerPlant()
        plant.publish(tick(1, 100.0))
        plant.publish(tick(1, 100.0))
        assert len(list(plant.tpipe())) == 1

    def test_sequences_are_tracked_per_instrument(self) -> None:
        """Two venues number their own streams; a shared counter would
        raise a false gap on every interleave."""
        plant = TickerPlant()
        plant.publish(tick(1, 100.0, subject="equity:IBM"))
        plant.publish(tick(1, 200.0, subject="equity:AAPL"))
        plant.publish(tick(2, 101.0, subject="equity:IBM"))
        assert plant.instruments == 2
