"""A realistic USD multi-curve environment, shared by the SWPM tests.

Deliberately *not* flat and not synthetic-round: the discount curve dips
through the 5Y and rises again, and the forecast curve sits a basis above
it. A flat market hides exactly the mistakes these tests exist to catch —
with equal curves the single-curve telescoping identity holds, so a pricer
that discounted off the forecast curve would pass every assertion.

Market conventions are set explicitly on the curve *and* on the trade,
because the cross-check in `test_swap.py` — the curve reprices its own
inputs when they are re-priced as trades — is only meaningful if the two
describe the same instrument.
"""

from __future__ import annotations

from datetime import date

import pytest

from treble.analytics._ql import DayCount, Market
from treble.analytics.curves.config import (
    CurveConfig,
    InstrumentKind,
    InstrumentSpec,
)
from treble.analytics.curves.multicurve import CurveSet, CurveSpec
from treble.analytics.derivatives.csa import CsaTerms

AS_OF = date(2026, 6, 30)
SPOT = date(2026, 7, 2)  # T+2 on the US settlement calendar
CALENDAR = Market.US_SETTLEMENT
NOTIONAL = 100_000_000.0

OIS_NAME = "USD-SOFR-OIS"
FORECAST_NAME = "USD-LIBOR-3M"
EUR_CSA_NAME = "USD-COLLAT-EUR"

#: Curve maturities as real dates, so a trade can be written to match a
#: curve instrument exactly. Read off the same calendar the curves use.
SWAP_MATURITY = {"2Y": date(2028, 7, 3), "5Y": date(2031, 7, 2), "10Y": date(2036, 7, 2)}

OIS_QUOTES = {
    (InstrumentKind.DEPOSIT, "1W"): 0.0430,
    (InstrumentKind.OIS, "1Y"): 0.0405,
    (InstrumentKind.OIS, "5Y"): 0.0375,
    (InstrumentKind.OIS, "10Y"): 0.0390,
}
FORECAST_QUOTES = {
    (InstrumentKind.DEPOSIT, "3M"): 0.0455,
    (InstrumentKind.SWAP, "2Y"): 0.0420,
    (InstrumentKind.SWAP, "5Y"): 0.0405,
    (InstrumentKind.SWAP, "10Y"): 0.0420,
}

OIS_CONFIG = CurveConfig(
    name=OIS_NAME,
    currency="USD",
    calendar=CALENDAR,
    instruments=(
        InstrumentSpec(kind=InstrumentKind.DEPOSIT, tenor="1W"),
        InstrumentSpec(kind=InstrumentKind.OIS, tenor="1Y"),
        InstrumentSpec(kind=InstrumentKind.OIS, tenor="5Y"),
        InstrumentSpec(kind=InstrumentKind.OIS, tenor="10Y"),
    ),
)

FORECAST_CONFIG = CurveConfig(
    name=FORECAST_NAME,
    currency="USD",
    calendar=CALENDAR,
    index_tenor="3M",
    discount_basis=OIS_NAME,
    # USD market convention: semiannual 30/360 fixed against quarterly
    # ACT/360 floating.
    swap_fixed_frequency=2,
    fixed_leg_day_count=DayCount.THIRTY_360,
    float_leg_day_count=DayCount.ACT_360,
    instruments=(
        InstrumentSpec(kind=InstrumentKind.DEPOSIT, tenor="3M"),
        InstrumentSpec(kind=InstrumentKind.SWAP, tenor="2Y"),
        InstrumentSpec(kind=InstrumentKind.SWAP, tenor="5Y"),
        InstrumentSpec(kind=InstrumentKind.SWAP, tenor="10Y"),
    ),
)


@pytest.fixture(scope="session")
def curves() -> CurveSet:
    """OIS discounting plus a 3M forecast curve built against it."""
    return CurveSet(
        AS_OF,
        [
            CurveSpec(FORECAST_CONFIG, FORECAST_QUOTES),
            CurveSpec(OIS_CONFIG, OIS_QUOTES),
        ],
    )


@pytest.fixture(scope="session")
def usd_csa() -> CsaTerms:
    """The ordinary case: USD cash collateral remunerated at SOFR."""
    return CsaTerms(collateral_currency="USD", discount_curve=OIS_NAME)


@pytest.fixture(scope="session")
def eur_collateral_market() -> CurveSet:
    """The same market plus a cross-currency-basis CSA discount curve.

    Session-scoped because building it re-solves every curve in the set, and
    the CSA tests use it three times. Safe to share: a `CurveSet` is
    immutable once built — `bumped()` returns a new set rather than
    mutating this one.
    """
    return CurveSet(
        AS_OF,
        [
            CurveSpec(OIS_CONFIG, OIS_QUOTES),
            CurveSpec(FORECAST_CONFIG, FORECAST_QUOTES),
            CurveSpec(
                OIS_CONFIG.model_copy(update={"name": EUR_CSA_NAME, "discount_basis": OIS_NAME}),
                {},
                basis_spreads={"1Y": -0.0020, "5Y": -0.0025, "10Y": -0.0030},
            ),
        ],
    )
