"""Callable analytics: YTW/YTC properties and lattice OAS (ADR-0003).

Properties from CLAUDE.md §7: yield-to-worst never exceeds any individual
yield-to-call or YTM; Z-spread >= OAS for a callable (the option cost);
lattice price/OAS round-trips.
"""

from datetime import date

import pytest

from treble.analytics._ql import BusinessDay, DayCount
from treble.analytics.bonds.callable import (
    effective_duration,
    lattice_price,
    oas,
    yield_to_call,
    yield_to_worst,
)
from treble.analytics.bonds.pricing import yield_from_price, z_spread
from treble.analytics.bonds.spec import CallSchedule, FixedBondSpec, Frequency
from treble.analytics.curves import (
    CurveConfig,
    InstrumentKind,
    InstrumentSpec,
    Interpolation,
    build_curve,
)

AS_OF = date(2024, 3, 15)  # Friday; a business day (see test_pricing note)
VOL = 0.01  # explicit user-supplied normal vol (ADR-0003)

CALLABLE_10Y = FixedBondSpec(
    coupon=0.06,
    frequency=Frequency.SEMIANNUAL,
    issue_date=date(2024, 3, 15),
    maturity=date(2034, 3, 15),
    day_count=DayCount.THIRTY_360,
    business_day=BusinessDay.UNADJUSTED,
    settlement_days=0,
    calls=(
        CallSchedule(start=date(2029, 3, 15), price=100.0),
        CallSchedule(start=date(2031, 3, 15), price=100.0),
    ),
)
BULLET_10Y = CALLABLE_10Y.model_copy(update={"calls": ()})

QUOTES = {
    (InstrumentKind.DEPOSIT, "6M"): 0.042,
    (InstrumentKind.OIS, "1Y"): 0.041,
    (InstrumentKind.OIS, "2Y"): 0.040,
    (InstrumentKind.OIS, "5Y"): 0.039,
    (InstrumentKind.OIS, "10Y"): 0.040,
    (InstrumentKind.OIS, "30Y"): 0.041,
}
CONFIG = CurveConfig(
    name="TEST-USD-CALLABLE",
    currency="USD",
    instruments=tuple(InstrumentSpec(kind=k, tenor=t) for k, t in QUOTES),
    interpolation=Interpolation.MONOTONE_CONVEX,
    settlement_days=0,
)


@pytest.fixture(scope="module")
def curve():  # type: ignore[no-untyped-def]
    return build_curve(CONFIG, QUOTES, as_of=AS_OF)


class TestYieldToWorst:
    def test_ytw_never_exceeds_any_candidate(self) -> None:
        for price in (90.0, 100.0, 110.0):
            ytw = yield_to_worst.__wrapped__(CALLABLE_10Y, price, as_of=AS_OF)  # type: ignore[attr-defined]
            ytm = yield_from_price.__wrapped__(CALLABLE_10Y, price, as_of=AS_OF)  # type: ignore[attr-defined]
            assert ytw <= ytm + 1e-12
            for i in range(len(CALLABLE_10Y.calls)):
                ytc = yield_to_call.__wrapped__(CALLABLE_10Y, price, i, as_of=AS_OF)  # type: ignore[attr-defined]
                assert ytw <= ytc + 1e-12

    def test_premium_bond_worst_is_call(self) -> None:
        # Deep premium: being called early at par is the worst outcome.
        ytw = yield_to_worst.__wrapped__(CALLABLE_10Y, 115.0, as_of=AS_OF)  # type: ignore[attr-defined]
        first_call = yield_to_call.__wrapped__(CALLABLE_10Y, 115.0, 0, as_of=AS_OF)  # type: ignore[attr-defined]
        assert ytw == pytest.approx(first_call, abs=1e-12)

    def test_discount_bond_worst_is_maturity(self) -> None:
        ytw = yield_to_worst.__wrapped__(CALLABLE_10Y, 85.0, as_of=AS_OF)  # type: ignore[attr-defined]
        ytm = yield_from_price.__wrapped__(CALLABLE_10Y, 85.0, as_of=AS_OF)  # type: ignore[attr-defined]
        assert ytw == pytest.approx(ytm, abs=1e-12)


@pytest.mark.golden
class TestOas:
    def test_z_spread_at_least_oas_for_callable(self, curve) -> None:  # type: ignore[no-untyped-def]
        price = 98.0
        z = z_spread.__wrapped__(CALLABLE_10Y, price, curve, as_of=AS_OF)  # type: ignore[attr-defined]
        option_adjusted = oas.__wrapped__(  # type: ignore[attr-defined]
            CALLABLE_10Y, price, curve, as_of=AS_OF, volatility=VOL
        )
        # Option cost = Z - OAS, positive for a callable (spec §10.2).
        assert z >= option_adjusted

    def test_price_oas_round_trip(self, curve) -> None:  # type: ignore[no-untyped-def]
        price = 97.5
        spread = oas.__wrapped__(  # type: ignore[attr-defined]
            CALLABLE_10Y, price, curve, as_of=AS_OF, volatility=VOL
        )
        recovered = lattice_price.__wrapped__(  # type: ignore[attr-defined]
            CALLABLE_10Y, spread, curve, as_of=AS_OF, volatility=VOL
        )
        assert recovered == pytest.approx(price, abs=1e-4)

    def test_higher_vol_lowers_callable_price(self, curve) -> None:  # type: ignore[no-untyped-def]
        # The investor is short the call: more rate vol = more option value
        # given away = lower bond price at the same spread.
        low = lattice_price.__wrapped__(  # type: ignore[attr-defined]
            CALLABLE_10Y, 0.005, curve, as_of=AS_OF, volatility=0.005
        )
        high = lattice_price.__wrapped__(  # type: ignore[attr-defined]
            CALLABLE_10Y, 0.005, curve, as_of=AS_OF, volatility=0.02
        )
        assert high < low

    def test_effective_duration_below_bullet_duration(self, curve) -> None:  # type: ignore[no-untyped-def]
        # The call caps upside: effective duration of the callable must sit
        # below the same-terms bullet's effective duration (§10.1).
        callable_ed = effective_duration.__wrapped__(  # type: ignore[attr-defined]
            CALLABLE_10Y, 99.0, curve, as_of=AS_OF, volatility=VOL
        )
        bullet_ed = effective_duration.__wrapped__(  # type: ignore[attr-defined]
            BULLET_10Y, 99.0, curve, as_of=AS_OF, volatility=VOL
        )
        assert 0.0 < callable_ed < bullet_ed

    def test_envelope_records_vol_assumption(self, curve) -> None:  # type: ignore[no-untyped-def]
        result = oas(CALLABLE_10Y, 98.0, curve, as_of=AS_OF, volatility=VOL)
        # ADR-0003: the stated-vol assumption must be visible on the output.
        assert result.parameters["volatility"] == repr(VOL)
        assert result.inputs["curve"] == CONFIG.content_hash
