"""The `SWPM` curve environment built from stored prints (spec §12.1).

Every test here is about a way of producing a *plausible* screen from
incoherent data. None of the failure modes raises on its own: a curve mixing
two trading days bootstraps fine, and a forecast curve extrapolated past its
discount curve gives smooth, sensible-looking, wrong numbers at the long end.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.swap_market import (
    DISCOUNT_CURVE,
    FORECAST_CURVE,
    MIN_NODES,
    SwapMarketUnavailableError,
    build_swap_market,
)

AS_OF = datetime(2026, 8, 2, 18, 0, tzinfo=UTC)
KNOWN = datetime(2026, 8, 1, 6, 0, tzinfo=UTC)
DAY = date(2026, 7, 31)
EARLIER = date(2026, 7, 30)

#: A realistic euro market: ESTR through 2.6-3.2%, EURIBOR a basis above it.
ESTR = {
    "2Y": 0.02708,
    "3Y": 0.02733,
    "5Y": 0.02780,
    "7Y": 0.02836,
    "10Y": 0.02970,
    "20Y": 0.03216,
    "30Y": 0.03179,
}
EURIBOR = {
    "2Y": 0.03011,
    "3Y": 0.03027,
    "5Y": 0.03050,
    "7Y": 0.03106,
    "10Y": 0.03218,
    "20Y": 0.03381,
    "30Y": 0.03302,
}


def _write(
    store: DuckStore,
    *,
    discount: dict[str, float],
    forecast: dict[str, float],
    day: date = DAY,
    forecast_day: date | None = None,
) -> None:
    provenance = Provenance(
        source_system="dtcc-sdr",
        source_uri="https://example.invalid/CFTC_CUMULATIVE_RATES_2026_07_31.zip",
        retrieved_at=KNOWN,
        method=ExtractionMethod.API,
        extractor_version="1",
        payload_hash="0" * 64,
    )
    facts = [
        Fact(
            subject=f"swap:{curve}:{tenor}",
            field="PAR_RATE",
            value=rate,
            effective_from=on,
            effective_to=on,
            knowledge_from=KNOWN,
            provenance_id=provenance.id,
        )
        for curve, rates, on in (
            (DISCOUNT_CURVE, discount, day),
            (FORECAST_CURVE, forecast, forecast_day or day),
        )
        for tenor, rate in rates.items()
    ]
    store.write_provenance([provenance])
    store.write_facts(facts)


@pytest.fixture
def store(tmp_path: Path) -> DuckStore:
    return DuckStore(tmp_path / "t.db")


class TestABuiltMarket:
    def test_it_builds_from_stored_prints(self, store: DuckStore) -> None:
        _write(store, discount=ESTR, forecast=EURIBOR)
        market = build_swap_market(store, as_of=AS_OF)
        assert market.report_date == DAY
        assert market.curves.names == (DISCOUNT_CURVE, FORECAST_CURVE)
        assert set(market.tenors) == set(ESTR)

    def test_the_forecast_curve_is_solved_against_the_discount_curve(
        self, store: DuckStore
    ) -> None:
        """Not two independent curves. The whole point of the pairing is
        that one discounts the other (spec §11.1)."""
        _write(store, discount=ESTR, forecast=EURIBOR)
        market = build_swap_market(store, as_of=AS_OF)
        forecast = market.curves.curve(FORECAST_CURVE)
        assert forecast.config.discount_basis == DISCOUNT_CURVE
        assert forecast.config.index_tenor == "6M"

    def test_only_the_forecast_curve_forecasts(self, store: DuckStore) -> None:
        """An overnight curve declaring an index tenor could be used as a
        forecast curve, which prices a daily-compounded rate on a discrete
        schedule."""
        _write(store, discount=ESTR, forecast=EURIBOR)
        market = build_swap_market(store, as_of=AS_OF)
        assert market.curves.curve(DISCOUNT_CURVE).config.index_tenor is None

    def test_the_basis_is_positive_at_every_tenor(self, store: DuckStore) -> None:
        """EURIBOR carries bank credit; ESTR is near risk-free. A sign flip
        would mean the two curves had been swapped, and a swapped pair still
        bootstraps and still looks like a market."""
        _write(store, discount=ESTR, forecast=EURIBOR)
        basis = build_swap_market(store, as_of=AS_OF).basis_bp
        assert all(0.0 < bp < 100.0 for bp in basis.values()), basis


class TestRefusals:
    def test_an_empty_store_refuses(self, store: DuckStore) -> None:
        with pytest.raises(SwapMarketUnavailableError, match="no trading day"):
            build_swap_market(store, as_of=AS_OF)

    def test_a_discount_curve_alone_refuses(self, store: DuckStore) -> None:
        """One curve is not a multi-curve environment, and a screen that
        fell back to single-curve here would be the exact defect the pricer
        exists to prevent."""
        _write(store, discount=ESTR, forecast={})
        with pytest.raises(SwapMarketUnavailableError, match="no trading day"):
            build_swap_market(store, as_of=AS_OF)

    def test_curves_from_different_days_are_not_combined(self, store: DuckStore) -> None:
        """A curve whose discounting is Thursday's and whose forwards are
        Friday's is smooth, sensible-looking and wrong."""
        _write(store, discount=ESTR, forecast=EURIBOR, day=EARLIER, forecast_day=DAY)
        with pytest.raises(SwapMarketUnavailableError, match="no trading day"):
            build_swap_market(store, as_of=AS_OF)

    def test_too_few_shared_tenors_refuses(self, store: DuckStore) -> None:
        thin = dict(list(ESTR.items())[:3])
        _write(store, discount=thin, forecast=EURIBOR)
        with pytest.raises(SwapMarketUnavailableError, match="shared tenors"):
            build_swap_market(store, as_of=AS_OF)

    def test_the_threshold_is_the_one_declared(self, store: DuckStore) -> None:
        """Guards the test above against the constant drifting away from
        what it checks."""
        exactly_enough = dict(list(ESTR.items())[:MIN_NODES])
        _write(store, discount=exactly_enough, forecast=EURIBOR)
        assert len(build_swap_market(store, as_of=AS_OF).tenors) == MIN_NODES


class TestOnlyCommonTenorsAreUsed:
    def test_a_forecast_node_past_the_discount_curve_is_dropped(self, store: DuckStore) -> None:
        """The forecast curve is discounted by the other one, so a node
        beyond its last would be discounted by extrapolation — which does
        not announce itself, it just makes the long end quietly wrong."""
        _write(store, discount=ESTR, forecast={**EURIBOR, "40Y": 0.0315})
        market = build_swap_market(store, as_of=AS_OF)
        assert "40Y" not in market.tenors
        assert set(market.tenors) == set(ESTR) & set(EURIBOR)

    def test_a_discount_node_with_no_forecast_quote_is_dropped(self, store: DuckStore) -> None:
        _write(store, discount={**ESTR, "1Y": 0.0260}, forecast=EURIBOR)
        assert "1Y" not in build_swap_market(store, as_of=AS_OF).tenors


class TestPointInTime:
    def test_a_read_before_the_facts_were_known_sees_no_market(self, store: DuckStore) -> None:
        """I2. The prints are published after the close of the day they
        describe, so a valuation dated before that must not see them —
        otherwise a backtest reads tomorrow's curve."""
        _write(store, discount=ESTR, forecast=EURIBOR)
        before = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
        with pytest.raises(SwapMarketUnavailableError):
            build_swap_market(store, as_of=before)

    def test_the_most_recent_usable_day_is_chosen(self, store: DuckStore) -> None:
        _write(store, discount=ESTR, forecast=EURIBOR, day=EARLIER)
        _write(store, discount=ESTR, forecast=EURIBOR, day=DAY)
        assert build_swap_market(store, as_of=AS_OF).report_date == DAY
