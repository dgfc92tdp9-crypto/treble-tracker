"""Straight-bond golden and property tests (CLAUDE.md §7).

Golden values here are *independently computed* in the test body from first
principles (plain discounting arithmetic on dates chosen so 30/360 gives
exact period fractions) — validating the QuantLib-backed pipeline against a
second implementation, not against itself. Published-reference goldens
(Treasury auction results) are added with the recorded Treasury fixtures in
the ingest work package, so the reference values enter the repo as data with
provenance rather than as numbers typed from memory.
"""

from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from treble.analytics._ql import BusinessDay, DayCount
from treble.analytics.bonds.pricing import (
    accrued_interest,
    cash_flows,
    convexity,
    dv01,
    macaulay_duration,
    modified_duration,
    price_from_yield,
    yield_from_price,
    z_spread,
)
from treble.analytics.bonds.spec import FixedBondSpec, Frequency
from treble.analytics.curves import (
    CurveConfig,
    InstrumentKind,
    InstrumentSpec,
    Interpolation,
)

AS_OF = date(2024, 3, 15)

# Dates chosen so 30/360 accruals are exactly 0.5 per period and settlement
# falls on a coupon date: the discounting arithmetic below is then exact.
# 2024-03-15 is a Friday and a US business day — settlement must not roll
# (2024-01-15 was MLK Day, which silently shifts settlement; CLAUDE.md §11
# calls out exactly this class of silent calendar error).
TWO_YEAR_8PCT = FixedBondSpec(
    coupon=0.08,
    frequency=Frequency.SEMIANNUAL,
    issue_date=date(2024, 3, 15),
    maturity=date(2026, 3, 15),
    day_count=DayCount.THIRTY_360,
    business_day=BusinessDay.UNADJUSTED,
    settlement_days=0,
)


class TestIndependentArithmeticGoldens:
    @pytest.mark.golden
    def test_two_year_semiannual_at_6pct(self) -> None:
        # Second implementation: plain discounting, 4 exact half-years.
        expected = sum(4.0 / 1.03**k for k in range(1, 5)) + 100.0 / 1.03**4
        result = price_from_yield(TWO_YEAR_8PCT, 0.06, as_of=AS_OF)
        assert result.value == pytest.approx(expected, abs=1e-9)
        # I3: the envelope identifies the model.
        assert result.model_id == "bonds.price_from_yield"
        assert result.parameters["yield_rate"] == "0.06"

    @pytest.mark.golden
    def test_zero_coupon_closed_form(self) -> None:
        zero_bond = TWO_YEAR_8PCT.model_copy(update={"coupon": 0.0})
        expected = 100.0 / 1.025**4
        assert price_from_yield(zero_bond, 0.05, as_of=AS_OF).value == pytest.approx(
            expected, abs=1e-9
        )

    @pytest.mark.golden
    def test_par_bond_prices_at_par(self) -> None:
        assert price_from_yield(TWO_YEAR_8PCT, 0.08, as_of=AS_OF).value == pytest.approx(
            100.0, abs=1e-9
        )

    def test_accrued_zero_on_coupon_date(self) -> None:
        assert accrued_interest(TWO_YEAR_8PCT, as_of=AS_OF).value == pytest.approx(0.0, abs=1e-12)

    def test_cash_flow_schedule(self) -> None:
        flows = cash_flows(TWO_YEAR_8PCT, as_of=AS_OF).value
        # 4 coupons + redemption (final date carries coupon + principal).
        amounts = [amount for _d, amount in flows]
        assert len(amounts) == 5
        assert amounts[:4] == pytest.approx([4.0] * 4, abs=1e-9)
        assert amounts[4] == pytest.approx(100.0, abs=1e-9)
        assert flows[-1][0] == date(2026, 3, 15)


class TestProperties:
    @settings(max_examples=25, deadline=None)
    @given(
        yield_rate=st.floats(min_value=0.001, max_value=0.15),
        coupon=st.floats(min_value=0.0, max_value=0.12),
        years=st.integers(min_value=1, max_value=30),
    )
    def test_price_yield_round_trip(self, yield_rate: float, coupon: float, years: int) -> None:
        spec = TWO_YEAR_8PCT.model_copy(
            update={"coupon": coupon, "maturity": date(2024 + years, 3, 15)}
        )
        price = price_from_yield.__wrapped__(spec, yield_rate, as_of=AS_OF)  # type: ignore[attr-defined]
        recovered = yield_from_price.__wrapped__(spec, price, as_of=AS_OF)  # type: ignore[attr-defined]
        assert recovered == pytest.approx(yield_rate, abs=1e-9)

    @settings(max_examples=15, deadline=None)
    @given(yield_rate=st.floats(min_value=0.005, max_value=0.12))
    def test_duration_identity(self, yield_rate: float) -> None:
        # Modified = Macaulay / (1 + y/f) — definitional identity.
        mac = macaulay_duration.__wrapped__(TWO_YEAR_8PCT, yield_rate, as_of=AS_OF)  # type: ignore[attr-defined]
        mod = modified_duration.__wrapped__(TWO_YEAR_8PCT, yield_rate, as_of=AS_OF)  # type: ignore[attr-defined]
        assert mod == pytest.approx(mac / (1.0 + yield_rate / 2.0), rel=1e-9)

    def test_price_decreasing_in_yield_and_convex(self) -> None:
        prices = [
            price_from_yield.__wrapped__(TWO_YEAR_8PCT, y / 1000.0, as_of=AS_OF)  # type: ignore[attr-defined]
            for y in range(10, 150, 5)
        ]
        from itertools import pairwise

        assert all(a > b for a, b in pairwise(prices))
        assert convexity(TWO_YEAR_8PCT, 0.05, as_of=AS_OF).value > 0.0

    def test_dv01_positive_and_consistent_with_duration(self) -> None:
        y = 0.06
        result_dv01 = dv01(TWO_YEAR_8PCT, y, as_of=AS_OF).value
        mod = modified_duration.__wrapped__(TWO_YEAR_8PCT, y, as_of=AS_OF)  # type: ignore[attr-defined]
        price = price_from_yield.__wrapped__(TWO_YEAR_8PCT, y, as_of=AS_OF)  # type: ignore[attr-defined]
        assert result_dv01 > 0.0
        # DV01 is close to modified duration * dirty price * 1bp (clean=dirty here).
        assert result_dv01 == pytest.approx(mod * price * 0.0001, rel=1e-4)


FLAT_QUOTES = {
    (InstrumentKind.DEPOSIT, "6M"): 0.04,
    (InstrumentKind.OIS, "1Y"): 0.04,
    (InstrumentKind.OIS, "2Y"): 0.04,
    (InstrumentKind.OIS, "5Y"): 0.04,
}
FLAT_CONFIG = CurveConfig(
    name="TEST-FLAT",
    currency="USD",
    instruments=tuple(InstrumentSpec(kind=k, tenor=t) for k, t in FLAT_QUOTES),
    interpolation=Interpolation.MONOTONE_CONVEX,
    settlement_days=0,
)


class TestZSpread:
    def test_z_spread_monotone_in_price(self) -> None:
        from treble.analytics.curves import build_curve

        curve = build_curve(FLAT_CONFIG, FLAT_QUOTES, as_of=AS_OF)
        spreads = [
            z_spread.__wrapped__(TWO_YEAR_8PCT, p, curve, as_of=AS_OF)  # type: ignore[attr-defined]
            for p in (95.0, 100.0, 105.0)
        ]
        assert spreads[0] > spreads[1] > spreads[2]

    def test_z_spread_zero_when_priced_off_the_curve(self) -> None:
        from math import exp

        from treble.analytics.curves import build_curve

        curve = build_curve(FLAT_CONFIG, FLAT_QUOTES, as_of=AS_OF)
        flows = cash_flows.__wrapped__(TWO_YEAR_8PCT, as_of=AS_OF)  # type: ignore[attr-defined]
        # Price the bond by discounting its flows on the curve directly
        # (independent arithmetic), then ask for the spread over that curve.
        from treble.analytics import _ql
        from treble.analytics._ql import to_ql_date

        dc = _ql.day_counter(TWO_YEAR_8PCT.day_count)
        model_price = sum(
            amount
            * exp(
                -curve.zero(dc.yearFraction(to_ql_date(AS_OF), to_ql_date(d)))
                * dc.yearFraction(to_ql_date(AS_OF), to_ql_date(d))
            )
            for d, amount in flows
        )
        spread = z_spread.__wrapped__(  # type: ignore[attr-defined]
            TWO_YEAR_8PCT, model_price, curve, as_of=AS_OF
        )
        assert spread == pytest.approx(0.0, abs=1e-8)

    def test_envelope_carries_curve_hash(self) -> None:
        from treble.analytics.curves import build_curve

        curve = build_curve(FLAT_CONFIG, FLAT_QUOTES, as_of=AS_OF)
        result = z_spread(TWO_YEAR_8PCT, 99.0, curve, as_of=AS_OF)
        # I4 -> I3: the curve identity is stamped on the analytic output.
        assert result.inputs["curve"] == FLAT_CONFIG.content_hash
