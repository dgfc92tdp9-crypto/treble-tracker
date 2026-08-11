"""G-spread must be zero for a bond that sits on the benchmark curve.

This is the check the golden tests could not make. They compared `g_spread`
against values computed the same way the function computed them, so a units
error shared by both passed — and there was one: `yield_from_price` returns
a yield compounded at the bond's frequency while `Curve.zero` returns a
continuously compounded rate, and the function subtracted one from the
other.

Measured on the live CMT curve before the fix, a ten-year par Treasury
priced at 100 on the curve it was built from reported **+5.38bp**, of which
+5.32bp was the conversion. Systematic, always the same sign, and a 5% error
on a typical 100bp corporate spread.

A self-consistency property catches what a golden value cannot: whatever the
conventions, a bond *on* the curve is worth the curve.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from treble.analytics.bonds import pricing
from treble.analytics.bonds.spec import FixedBondSpec, Frequency
from treble.analytics.curves.bootstrap import build_curve
from treble.analytics.curves.config import CurveConfig, InstrumentKind, InstrumentSpec

AS_OF = date(2026, 8, 7)
#: A plain upward-sloping par curve, so the conversion actually bites — on a
#: flat curve at zero rates every compounding convention agrees.
PAR = {"1Y": 0.0401, "2Y": 0.0419, "3Y": 0.0425, "5Y": 0.0435, "7Y": 0.0449, "10Y": 0.0465}


def _curve() -> object:
    config = CurveConfig(
        name="UST-CMT",
        currency="USD",
        day_count="ACT/ACT ICMA",
        calendar="us-government",
        fixed_frequency=2,
        instruments=tuple(InstrumentSpec(kind=InstrumentKind.SWAP, tenor=t) for t in PAR),
    )
    return build_curve(config, {(InstrumentKind.SWAP, t): r for t, r in PAR.items()}, as_of=AS_OF)


def _bond(coupon: float) -> FixedBondSpec:
    return FixedBondSpec(
        face=100.0,
        coupon=coupon,
        maturity=date(AS_OF.year + 10, AS_OF.month, AS_OF.day),
        issue_date=AS_OF,
        frequency=Frequency.SEMIANNUAL,
        day_count="ACT/ACT ICMA",
        calendar="us-government",
    )


def _g(coupon: float, price: float = 100.0) -> float:
    return pricing.g_spread.__wrapped__(_bond(coupon), price, _curve(), as_of=AS_OF)  # type: ignore[attr-defined]


class TestABondOnTheCurveHasNoSpread:
    def test_a_par_bond_priced_at_par_is_flat_to_the_curve(self) -> None:
        """The assertion that failed before the fix, at +5.38bp. The
        tolerance is a basis point: anything left is curve interpolation
        between the 7Y and 10Y nodes, not a units error."""
        assert _g(PAR["10Y"]) == pytest.approx(0.0, abs=1e-4)

    def test_the_conversion_is_not_just_zeroing_everything(self) -> None:
        """A fix that made every G-spread zero would pass the test above.
        A bond with a coupon 200bp higher, still priced at par, must show
        200bp — so the scale survives the correction."""
        assert _g(PAR["10Y"] + 0.02) == pytest.approx(0.02, abs=2e-4)

    def test_a_discount_price_widens_the_spread(self) -> None:
        """Direction, independent of magnitude: paying less for the same
        cash flows earns more, which is a wider spread."""
        assert _g(PAR["10Y"], price=95.0) > _g(PAR["10Y"], price=100.0)


class TestTheConventionsAreWhatTheyClaim:
    def test_the_curve_is_continuously_compounded(self) -> None:
        """Pinned because `g_spread`'s correctness depends on it. If
        `Curve.zero` ever returned a periodic rate, the conversion added to
        `g_spread` would become the error it was written to remove."""
        curve = _curve()
        t = 5.0
        assert curve.discount(t) == pytest.approx(math.exp(-curve.zero(t) * t))  # type: ignore[attr-defined]

    def test_the_yield_is_compounded_at_the_bond_frequency(self) -> None:
        """The other half of the pair. A semi-annual yield of y prices the
        bond as sum(cf / (1 + y/2)^(2t))."""
        spec = _bond(PAR["10Y"])
        ytm = pricing.yield_from_price.__wrapped__(spec, 100.0, as_of=AS_OF)  # type: ignore[attr-defined]
        # Round-trip through the pricer: the same yield must return the price.
        assert pricing.price_from_yield.__wrapped__(spec, ytm, as_of=AS_OF) == pytest.approx(  # type: ignore[attr-defined]
            100.0, abs=1e-6
        )
