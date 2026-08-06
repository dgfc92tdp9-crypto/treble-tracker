"""`mktbar` and `mktvwap` aggregation (spec §8.3).

Two kinds of check. Synthetic ticks with a known answer prove the arithmetic;
the 40 frames recorded live from Coinbase prove it survives real data, where
sizes are 1.3e-07 and several prints share a timestamp to the microsecond.

The refusals matter more than the arithmetic here. A VWAP that quietly
weights unsized ticks equally is a simple average under a volume-weighted
name, and nothing downstream can tell — it is exactly the number a large
print moves and a simple average does not.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from treble.core.identifiers import TUID
from treble.plant.bars import EPOCH, NoTicksError, bars_from_ticks, vwap_over
from treble.plant.conflation import Tick
from treble.plant.venues import ticks_from_messages

FIXTURE = Path(__file__).parent.parent / "fixtures" / "coinbase" / "ws_matches.jsonl"
MINUTE = timedelta(minutes=1)
BASE = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _tick(seconds: float, price: float, size: float | None = 1.0, sequence: int = 0) -> Tick:
    return Tick(
        subject=TUID("crypto:coinbase:BTC-USD"),
        field="PX_LAST",
        value=price,
        sequence=sequence or int(seconds * 1000) + 1,
        exchange_time=BASE + timedelta(seconds=seconds),
        size=size,
    )


class TestTheArithmetic:
    def test_open_high_low_close_come_from_the_right_ticks(self) -> None:
        ticks = [_tick(1, 100.0), _tick(2, 105.0), _tick(3, 95.0), _tick(4, 102.0)]
        bar = bars_from_ticks(ticks, interval=MINUTE, now=BASE + timedelta(hours=1))[0]
        assert (bar.open, bar.high, bar.low, bar.close) == (100.0, 105.0, 95.0, 102.0)
        assert bar.trades == 4

    def test_vwap_weights_by_size(self) -> None:
        """The whole point: one large print at 100 and one tiny at 200 gives
        a VWAP near 100, where a simple average gives 150."""
        ticks = [_tick(1, 100.0, size=99.0), _tick(2, 200.0, size=1.0)]
        result = vwap_over(ticks)
        assert result.price == pytest.approx(101.0)
        assert result.volume == pytest.approx(100.0)
        simple = sum(t.value for t in ticks) / len(ticks)
        assert abs(result.price - simple) > 40, "a simple average would have been 150"

    def test_a_bars_vwap_matches_vwap_over_its_own_ticks(self) -> None:
        """The two entry points must not disagree about one window."""
        ticks = [_tick(i, 100.0 + i, size=float(i + 1)) for i in range(5)]
        bar = bars_from_ticks(ticks, interval=MINUTE, now=BASE + timedelta(hours=1))[0]
        assert bar.vwap == pytest.approx(vwap_over(ticks).price)

    def test_ordering_is_by_sequence_not_timestamp(self) -> None:
        """Two prints can share a venue timestamp; the sequence is what the
        venue says came first. Ordering by time would make open and close
        depend on the sort's stability."""
        same = BASE + timedelta(seconds=5)
        ticks = [
            Tick(subject=TUID("x:y"), field="PX_LAST", value=10.0, sequence=2, exchange_time=same),
            Tick(subject=TUID("x:y"), field="PX_LAST", value=20.0, sequence=1, exchange_time=same),
        ]
        bar = bars_from_ticks(ticks, interval=MINUTE, now=BASE + timedelta(hours=1))[0]
        assert (bar.open, bar.close) == (20.0, 10.0)


class TestBoundaries:
    def test_bars_are_cut_on_fixed_boundaries_not_on_the_first_tick(self) -> None:
        """Boundaries derived from the first tick seen would shift the whole
        grid the moment an earlier tick arrived, so the same ticks would
        produce different bars on a replay."""
        ticks = [_tick(30, 100.0), _tick(90, 101.0)]
        bars = bars_from_ticks(ticks, interval=MINUTE, now=BASE + timedelta(hours=1))
        assert len(bars) == 2
        assert bars[0].start == BASE
        assert bars[1].start == BASE + MINUTE
        assert (bars[0].start - EPOCH) % MINUTE == timedelta(0)

    def test_the_unfinished_interval_is_marked_partial(self) -> None:
        """Its close is not a close. Shown beside completed bars without a
        flag, a chart draws a spurious drop on every refresh."""
        ticks = [_tick(10, 100.0), _tick(70, 101.0)]
        now = BASE + timedelta(seconds=80)
        bars = bars_from_ticks(ticks, interval=MINUTE, now=now)
        assert bars[0].complete is True
        assert bars[-1].complete is False

    def test_two_instruments_do_not_share_a_bar(self) -> None:
        """A bar whose open came from one instrument and close from another
        would be a plausible-looking price for nothing that trades."""
        moment = BASE + timedelta(seconds=5)
        ticks = [
            Tick(
                subject=TUID("a:1"), field="PX_LAST", value=10.0, sequence=1, exchange_time=moment
            ),
            Tick(
                subject=TUID("b:2"), field="PX_LAST", value=99.0, sequence=1, exchange_time=moment
            ),
        ]
        bars = bars_from_ticks(ticks, interval=MINUTE, now=BASE + timedelta(hours=1))
        assert len(bars) == 2
        assert {str(b.subject) for b in bars} == {"a:1", "b:2"}


class TestItRefusesRatherThanSubstituting:
    def test_unsized_ticks_give_no_vwap_rather_than_a_simple_average(self) -> None:
        ticks = [_tick(1, 100.0, size=None), _tick(2, 200.0, size=None)]
        bar = bars_from_ticks(ticks, interval=MINUTE, now=BASE + timedelta(hours=1))[0]
        assert bar.vwap is None
        assert bar.volume is None
        # …and the bar is still a bar: prices are known even when size is not.
        assert bar.close == 200.0

    def test_a_bar_mixing_sized_and_unsized_ticks_reports_no_vwap(self) -> None:
        """Weighting over part of the bar's own volume is a number with no
        meaning and no way to notice it."""
        ticks = [_tick(1, 100.0, size=5.0), _tick(2, 200.0, size=None)]
        bar = bars_from_ticks(ticks, interval=MINUTE, now=BASE + timedelta(hours=1))[0]
        assert bar.vwap is None
        assert bar.volume is None

    def test_vwap_over_unsized_ticks_raises_with_the_reason(self) -> None:
        with pytest.raises(ValueError, match="volume-weighted name"):
            vwap_over([_tick(1, 100.0, size=None)])

    def test_vwap_across_instruments_is_refused(self) -> None:
        moment = BASE + timedelta(seconds=1)
        ticks = [
            Tick(
                subject=TUID("a:1"),
                field="PX_LAST",
                value=10.0,
                sequence=1,
                exchange_time=moment,
                size=1.0,
            ),
            Tick(
                subject=TUID("b:2"),
                field="PX_LAST",
                value=99.0,
                sequence=1,
                exchange_time=moment,
                size=1.0,
            ),
        ]
        with pytest.raises(ValueError, match="has no meaning"):
            vwap_over(ticks)

    def test_no_ticks_is_an_error_not_an_empty_series(self) -> None:
        """'No trades in this window' and 'nothing has ever printed here' are
        different answers, and a chart drawing neither cannot tell them apart."""
        with pytest.raises(NoTicksError):
            bars_from_ticks([], interval=MINUTE)
        with pytest.raises(NoTicksError):
            vwap_over([])

    def test_a_non_positive_interval_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            bars_from_ticks([_tick(1, 100.0)], interval=timedelta(0))

    def test_zero_volume_is_refused(self) -> None:
        with pytest.raises(ValueError, match="nothing to weight by"):
            vwap_over([_tick(1, 100.0, size=0.0)])


class TestAgainstTheRecordedFeed:
    """Real Coinbase prints: sizes of 1.3e-07, shared microsecond timestamps."""

    def test_bars_build_from_the_recorded_stream(self) -> None:
        ticks = list(ticks_from_messages(FIXTURE.read_text().splitlines()))
        bars = bars_from_ticks(ticks, interval=MINUTE)
        assert bars
        assert sum(bar.trades for bar in bars) == len(ticks)
        for bar in bars:
            assert bar.low <= bar.open <= bar.high
            assert bar.low <= bar.close <= bar.high

    def test_every_recorded_bar_has_a_vwap(self) -> None:
        """Coinbase reports size on every match, so a missing VWAP here would
        mean the adapter dropped it."""
        ticks = list(ticks_from_messages(FIXTURE.read_text().splitlines()))
        bars = bars_from_ticks(ticks, interval=MINUTE)
        assert all(bar.vwap is not None for bar in bars)
        for bar in bars:
            assert bar.vwap is not None
            assert bar.low <= bar.vwap <= bar.high, "a VWAP outside the bar's range"

    def test_tiny_sizes_do_not_collapse_the_weighting(self) -> None:
        """Sizes here are ~1e-07. A VWAP that lost precision would land on
        the simple average or on a boundary price."""
        ticks = [
            t
            for t in ticks_from_messages(FIXTURE.read_text().splitlines())
            if str(t.subject).endswith("BTC-USD")
        ]
        result = vwap_over(ticks)
        assert result.volume > 0
        assert min(t.value for t in ticks) <= result.price <= max(t.value for t in ticks)
