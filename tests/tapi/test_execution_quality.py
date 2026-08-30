"""The join from the book to the price series.

The unit tests in `tests/analytics/test_tca.py` cover the arithmetic. What
this covers is the seam: that a fill written by the EMS is found, that its
trade date comes from the fact rather than from whoever asked, and — the
part that matters most — that a fill which cannot be scored is *reported*
rather than dropped.

Dropping it would be the quiet failure. Executions with no close are those
on days the series does not cover, which is not a random sample of the book,
so excluding them silently biases every average over what remains while the
report still says "average slippage".
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from treble.analytics.tca import UNAVAILABLE
from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.ems.executions import Execution, execution_facts
from treble.store.duck import DuckStore
from treble.tapi.tca import NOT_COMPUTED, execution_quality, unavailable_reason

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
TRADE = datetime(2026, 8, 28, 15, 30, tzinfo=UTC)


def _provenance(store: DuckStore) -> str:
    record = Provenance(
        source_system="ems",
        source_uri="fix://SIM/TREBLE",
        retrieved_at=TRADE,
        method=ExtractionMethod.FEED,
        extractor_version="1",
        payload_hash="a" * 64,
    )
    store.write_provenance([record])
    return record.id


def _fill(
    store: DuckStore,
    *,
    exec_id: str = "E1",
    symbol: str = "IBM",
    side: str = "buy",
    price: float = 101.0,
    qty: float = 100.0,
) -> None:
    execution = Execution(
        exec_id=exec_id,
        order_id="ORD1",
        symbol=symbol,
        side=side,
        last_qty=qty,
        last_px=price,
        cum_qty=qty,
        average_price=price,
        order_qty=qty,
        transact_time=TRADE,
    )
    store.write_facts(list(execution_facts(execution, _provenance(store))))


def _closes(store: DuckStore, symbol: str, points: dict[date, float]) -> None:
    record = Provenance(
        source_system="twelvedata",
        source_uri="https://example.invalid/px",
        retrieved_at=NOW,
        method=ExtractionMethod.API,
        extractor_version="1",
        payload_hash="b" * 64,
    )
    store.write_provenance([record])
    store.write_facts(
        [
            Fact(
                subject=TUID(f"equity:{symbol}"),
                field="ADJ_CLOSE",
                value=price,
                effective_from=day,
                effective_to=day,
                knowledge_from=NOW,
                provenance_id=record.id,
            )
            for day, price in points.items()
        ]
    )


class TestMeasuring:
    def test_a_fill_is_scored_against_its_trade_date_close(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        store = DuckStore(tmp_path / "s.db")
        _fill(store, price=101.0)
        _closes(store, "IBM", {date(2026, 8, 28): 100.0})

        quality = execution_quality(store, as_of=NOW)
        assert quality.fills == 1
        assert len(quality.measured) == 1
        assert quality.measured[0].slippage_bp == 100.0
        assert quality.total_cost == 100.0  # 100 shares, $1 over

    def test_the_trade_date_comes_from_the_fact_not_from_as_of(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """`as_of` is when someone asked. Using it as the trade date would
        score every fill against today's close whenever the report is run."""
        store = DuckStore(tmp_path / "s.db")
        _fill(store)
        _closes(store, "IBM", {date(2026, 8, 28): 100.0, date(2026, 8, 29): 200.0})

        quality = execution_quality(store, as_of=NOW)
        assert quality.measured[0].trade_date == date(2026, 8, 28)
        assert quality.measured[0].close_price == 100.0

    def test_a_sell_is_scored_as_a_sell_through_the_join(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        store = DuckStore(tmp_path / "s.db")
        _fill(store, side="sell", price=99.0)
        _closes(store, "IBM", {date(2026, 8, 28): 100.0})
        assert execution_quality(store, as_of=NOW).measured[0].slippage_bp == 100.0

    def test_several_fills_average(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        store = DuckStore(tmp_path / "s.db")
        _fill(store, exec_id="E1", price=101.0)
        _fill(store, exec_id="E2", price=99.0)
        _closes(store, "IBM", {date(2026, 8, 28): 100.0})
        quality = execution_quality(store, as_of=NOW)
        assert len(quality.measured) == 2
        assert quality.average_slippage_bp == 0.0


class TestWhatCannotBeMeasured:
    def test_a_fill_with_no_price_series_is_reported_not_dropped(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """The quiet failure this exists to prevent."""
        store = DuckStore(tmp_path / "s.db")
        _fill(store, symbol="NOPRICE")

        quality = execution_quality(store, as_of=NOW)
        assert quality.measured == ()
        assert len(quality.unmeasured) == 1
        assert quality.unmeasured[0].symbol == "NOPRICE"
        assert quality.fills == 1, "the fill is still counted"

    def test_a_fill_on_a_day_the_series_misses_is_reported(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        store = DuckStore(tmp_path / "s.db")
        _fill(store)
        _closes(store, "IBM", {date(2026, 8, 27): 100.0})  # the day before only
        quality = execution_quality(store, as_of=NOW)
        assert quality.measured == ()
        assert "no close for" in quality.unmeasured[0].reason

    def test_the_average_covers_only_what_was_measured(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """And `fills` still reports the whole book, so a reader can see
        the average was computed over a subset."""
        store = DuckStore(tmp_path / "s.db")
        _fill(store, exec_id="E1", symbol="IBM", price=101.0)
        _fill(store, exec_id="E2", symbol="NOPRICE", price=50.0)
        _closes(store, "IBM", {date(2026, 8, 28): 100.0})

        quality = execution_quality(store, as_of=NOW)
        assert quality.average_slippage_bp == 100.0
        assert (len(quality.measured), len(quality.unmeasured), quality.fills) == (1, 1, 2)

    def test_an_install_that_has_never_traded_is_empty_not_an_error(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """No fills is a true and unremarkable state. A screen saying 'no
        fills recorded' is right where one showing an error would mislead."""
        quality = execution_quality(DuckStore(tmp_path / "s.db"), as_of=NOW)
        assert quality.fills == 0
        assert quality.average_slippage_bp is None
        assert quality.total_cost == 0.0


class TestTheRefusalsTravel:
    def test_the_three_unavailable_benchmarks_come_back_with_the_result(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Carried in the result so a screen cannot render the one computed
        number as though it were the whole of TCA."""
        store = DuckStore(tmp_path / "s.db")
        _fill(store)
        _closes(store, "IBM", {date(2026, 8, 28): 100.0})
        quality = execution_quality(store, as_of=NOW)
        assert set(quality.unavailable) == {"vwap", "arrival", "implementation_shortfall"}

    def test_they_are_present_even_when_nothing_was_traded(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        quality = execution_quality(DuckStore(tmp_path / "s.db"), as_of=NOW)
        assert set(quality.unavailable) == {"vwap", "arrival", "implementation_shortfall"}


class TestTheUnavailableLookup:
    """Moved here from the analytics tests, with the function.

    The I3 gate was right to refuse it there: every public callable under
    `analytics/` should be a `@model`, and "why can this install not compute
    VWAP" is a question about what is held, not an analytic. TAPI is where
    that is known — and `execution_quality` calls it for every name it
    reports, so the typo guard runs in production rather than only here.
    """

    def test_every_reported_benchmark_has_a_reason(self) -> None:
        for benchmark in NOT_COMPUTED:
            assert len(unavailable_reason(benchmark)) > 40

    def test_arrival_blames_the_order_store_first(self) -> None:
        """Blocked on a record this repository could build, not only on
        market data it cannot get. Naming the nearer blocker is what makes
        the note actionable."""
        assert "order" in unavailable_reason("arrival")

    def test_shortfall_says_it_inherits_arrivals_blocker(self) -> None:
        assert "arrival" in unavailable_reason("implementation_shortfall")

    def test_an_unknown_benchmark_raises(self) -> None:
        """A typo in a screen would otherwise render as a plausible-looking
        explanation for a benchmark that does not exist."""
        with pytest.raises(KeyError, match="not a benchmark"):
            unavailable_reason("twap")

    def test_what_is_reported_matches_what_is_declared(self) -> None:
        """`NOT_COMPUTED` is listed rather than derived from the mapping, so
        a name in one and not the other is a visible difference."""
        assert set(NOT_COMPUTED) == set(UNAVAILABLE)
