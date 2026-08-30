"""Execution slippage against the close.

**The sign is the whole test.** A benchmark computed without the side is
right half the time and never announces which half: the same absolute
difference is a cost on a buy and a saving on a sell, and a report that got
it backwards would praise the worst executions. So every assertion below
that checks a buy has a partner checking the sell, and one checks that the
two disagree — because a function returning `abs(difference)` passes a
buy-only suite perfectly.
"""

from __future__ import annotations

from datetime import date

import pytest

from treble.analytics.tca import UNAVAILABLE, BenchmarkUnavailableError, close_benchmark

TRADE_DATE = date(2026, 8, 28)
CLOSES = {date(2026, 8, 27): 99.0, TRADE_DATE: 100.0, date(2026, 8, 31): 101.0}


def _bench(**kwargs: object):  # type: ignore[no-untyped-def]
    defaults: dict[str, object] = {
        "symbol": "IBM",
        "trade_date": TRADE_DATE,
        "side": "buy",
        "executed_price": 100.0,
        "quantity": 1000.0,
        "closes": CLOSES,
        "basis": "daily close, split and dividend adjusted (total return)",
    }
    return close_benchmark(**{**defaults, **kwargs}).value  # type: ignore[arg-type]


class TestSign:
    """Positive is cost. Getting this backwards praises bad executions."""

    def test_a_buy_above_the_close_is_a_cost(self) -> None:
        assert _bench(side="buy", executed_price=101.0).slippage_bp == pytest.approx(100.0)

    def test_a_buy_below_the_close_is_a_saving(self) -> None:
        assert _bench(side="buy", executed_price=99.0).slippage_bp == pytest.approx(-100.0)

    def test_a_sell_below_the_close_is_a_cost(self) -> None:
        """The half a buy-only suite misses."""
        assert _bench(side="sell", executed_price=99.0).slippage_bp == pytest.approx(100.0)

    def test_a_sell_above_the_close_is_a_saving(self) -> None:
        assert _bench(side="sell", executed_price=101.0).slippage_bp == pytest.approx(-100.0)

    def test_the_two_sides_disagree_on_the_same_fill(self) -> None:
        """`abs(difference)` passes every single-sided test above. This is
        what it cannot pass."""
        buy = _bench(side="buy", executed_price=101.0).slippage_bp
        sell = _bench(side="sell", executed_price=101.0).slippage_bp
        assert buy == pytest.approx(-sell)
        assert buy > 0 > sell

    def test_a_fill_at_the_close_has_no_slippage(self) -> None:
        for side in ("buy", "sell"):
            assert _bench(side=side, executed_price=100.0).slippage_bp == pytest.approx(0.0)

    def test_sell_short_is_a_sell(self) -> None:
        """`ems.executions` maps FIX side 5 to `sell_short`. Scoring it as a
        buy would invert the cost on every short."""
        assert _bench(side="sell_short", executed_price=99.0).slippage_bp == pytest.approx(100.0)


class TestCost:
    def test_cost_is_signed_like_slippage(self) -> None:
        """Both must agree, or a report showing a positive cost beside a
        negative slippage is unreadable."""
        for side, price in (("buy", 101.0), ("sell", 99.0)):
            result = _bench(side=side, executed_price=price, quantity=1000.0)
            assert result.cost > 0 and result.slippage_bp > 0

    def test_cost_scales_with_quantity(self) -> None:
        small = _bench(executed_price=101.0, quantity=100.0).cost
        large = _bench(executed_price=101.0, quantity=1000.0).cost
        assert large == pytest.approx(small * 10)

    def test_slippage_does_not_scale_with_quantity(self) -> None:
        """Basis points are per unit; a bigger order at the same price is
        the same execution quality, at greater cost."""
        assert _bench(executed_price=101.0, quantity=100.0).slippage_bp == pytest.approx(
            _bench(executed_price=101.0, quantity=100_000.0).slippage_bp
        )


class TestBasisPoints:
    def test_the_same_absolute_move_differs_by_price_level(self) -> None:
        """Two cents on a $3 stock and on a $300 stock are not the same
        execution, which is why this is not reported in currency alone."""
        cheap = close_benchmark(
            symbol="X",
            trade_date=TRADE_DATE,
            side="buy",
            executed_price=3.02,
            quantity=1.0,
            closes={TRADE_DATE: 3.00},
            basis="b",
        ).value
        dear = close_benchmark(
            symbol="Y",
            trade_date=TRADE_DATE,
            side="buy",
            executed_price=300.02,
            quantity=1.0,
            closes={TRADE_DATE: 300.00},
            basis="b",
        ).value
        assert cheap.slippage_bp > dear.slippage_bp * 50


class TestRefusals:
    def test_a_missing_close_is_refused_not_substituted(self) -> None:
        """**The important refusal.** Taking a neighbouring day's close
        would score a fill against a price struck on another day, and the
        result is indistinguishable from a correct one in every report it
        reaches. The series here holds the day before and the day after."""
        with pytest.raises(BenchmarkUnavailableError, match="no close for"):
            _bench(trade_date=date(2026, 8, 30))

    def test_the_refusal_names_the_date_and_the_series_size(self) -> None:
        with pytest.raises(BenchmarkUnavailableError, match="2026-08-30"):
            _bench(trade_date=date(2026, 8, 30))

    def test_an_empty_series_is_refused(self) -> None:
        with pytest.raises(BenchmarkUnavailableError):
            _bench(closes={})

    def test_a_non_positive_execution_price_is_refused(self) -> None:
        with pytest.raises(BenchmarkUnavailableError, match="not positive"):
            _bench(executed_price=0.0)

    def test_a_non_positive_benchmark_is_refused(self) -> None:
        """Basis points of zero are meaningless, and dividing would raise
        somewhere less informative."""
        benchmark = close_benchmark(
            symbol="X",
            trade_date=TRADE_DATE,
            side="buy",
            executed_price=100.0,
            quantity=1.0,
            closes={TRADE_DATE: 0.0},
            basis="b",
        ).value
        with pytest.raises(BenchmarkUnavailableError, match="not positive"):
            _ = benchmark.slippage_bp


class TestWhatIsNotComputed:
    """Three of §18.5's four benchmarks, declared as data here and looked
    up through `tapi.tca.unavailable_reason` — see
    `tests/tapi/test_execution_quality.py` for the lookup's own refusals."""

    def test_the_spec_s_four_benchmarks_are_all_accounted_for(self) -> None:
        """§18.5 names arrival, VWAP, close and implementation shortfall.
        One is computed and three are refused; none is silently absent."""
        assert set(UNAVAILABLE) == {"vwap", "arrival", "implementation_shortfall"}

    def test_every_reason_is_a_reason_not_a_label(self) -> None:
        for benchmark, reason in UNAVAILABLE.items():
            assert len(reason) > 40, f"{benchmark}: too short to be a reason"


class TestTheEnvelopeAndTheCaveat:
    def test_it_returns_a_model_result(self) -> None:
        """I3: every analytic is identified and versioned."""
        result = close_benchmark(
            symbol="IBM",
            trade_date=TRADE_DATE,
            side="buy",
            executed_price=100.0,
            quantity=1.0,
            closes=CLOSES,
            basis="b",
        )
        assert (result.model_id, result.model_version) == ("tca.close", "1")

    def test_the_caveat_travels_with_the_result(self) -> None:
        """On the object, so a screen prints it. A caveat that lives only in
        a docstring is one the person reading the report never sees."""
        assert "close" in _bench().caveat
        assert "not a measure of the decision" in _bench().caveat.lower()

    def test_the_basis_is_carried_through(self) -> None:
        """`ADJ_CLOSE` and `PX_LAST` are different benchmarks: one adjusted
        for a split falling between the trade and today would score the fill
        against a price the market never saw."""
        assert (
            _bench(basis="published level, not adjusted").basis == "published level, not adjusted"
        )
