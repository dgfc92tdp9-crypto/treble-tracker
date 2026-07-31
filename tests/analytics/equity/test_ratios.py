"""Equity ratios (spec §14.1).

A ratio is a division, and division is the most reliable way to produce a
confident, plausible, wrong figure. Most of these tests are about the
refusals, because those are the cases where the arithmetic succeeds and the
answer misleads.
"""

from __future__ import annotations

import pytest

from treble.analytics.equity.ratios import (
    UndefinedRatioError,
    book_value_per_share,
    growth,
    leverage,
    net_margin,
    return_on_assets,
    return_on_equity,
)


class TestAgainstRealFigures:
    """IBM's FY2025 as filed: revenue 67,535, net income 10,593, assets
    151,880, equity 34,452 (millions), 942,134,390 shares."""

    def test_net_margin(self) -> None:
        assert net_margin(10_593, 67_535).value == pytest.approx(0.1568, abs=1e-4)

    def test_return_on_equity(self) -> None:
        assert return_on_equity(10_593, 34_452).value == pytest.approx(0.3075, abs=1e-4)

    def test_return_on_assets(self) -> None:
        assert return_on_assets(10_593, 151_880).value == pytest.approx(0.0697, abs=1e-4)

    def test_leverage(self) -> None:
        assert leverage(151_880, 34_452).value == pytest.approx(4.409, abs=1e-3)

    def test_book_value_per_share(self) -> None:
        value = book_value_per_share(34_452_000_000, 942_134_390).value
        assert value == pytest.approx(36.57, abs=0.01)

    def test_the_identity_holds(self) -> None:
        """ROA x leverage = ROE. Two independently computed ratios that must
        agree; a unit slip in either shows up here rather than on a screen."""
        roa = return_on_assets(10_593, 151_880).value
        lev = leverage(151_880, 34_452).value
        assert roa * lev == pytest.approx(return_on_equity(10_593, 34_452).value, rel=1e-9)


class TestNegativeEquityIsRefused:
    def test_return_on_equity_refuses(self) -> None:
        """The dangerous case: a loss on negative equity gives a *positive*
        ROE, which reads as profitability. Boeing and Starbucks have both
        been there."""
        with pytest.raises(UndefinedRatioError, match="negative"):
            return_on_equity(-500, -1_000)

    def test_the_arithmetic_would_have_looked_fine(self) -> None:
        """Demonstrates what is being prevented: the raw division returns a
        healthy-looking 50% for an insolvent, loss-making company."""
        assert (-500) / (-1_000) == 0.5

    def test_leverage_refuses(self) -> None:
        with pytest.raises(UndefinedRatioError, match="negative"):
            leverage(1_000, -100)


class TestZeroDenominators:
    def test_margin_on_no_revenue_refuses(self) -> None:
        """A pre-revenue company would otherwise return infinity, which
        formats as a number."""
        with pytest.raises(UndefinedRatioError, match="zero"):
            net_margin(-50, 0)

    def test_book_value_needs_positive_shares(self) -> None:
        with pytest.raises(UndefinedRatioError, match="positive"):
            book_value_per_share(1_000, 0)


class TestGrowth:
    def test_ordinary_growth(self) -> None:
        assert growth(110, 100).value == pytest.approx(0.10)

    def test_decline(self) -> None:
        assert growth(90, 100).value == pytest.approx(-0.10)

    def test_a_zero_base_refuses(self) -> None:
        with pytest.raises(UndefinedRatioError, match="zero"):
            growth(50, 0)

    def test_a_turnaround_refuses(self) -> None:
        """-100 to +50 is not "150% growth": the formula returns -1.5, whose
        sign says the opposite of what happened."""
        with pytest.raises(UndefinedRatioError, match="straddle"):
            growth(50, -100)

    def test_growth_between_two_losses_narrowing(self) -> None:
        """Both negative, so the sign is meaningful: a loss narrowing from
        -100 to -50 is +50%."""
        assert growth(-50, -100).value == pytest.approx(0.5)


class TestModelEnvelope:
    def test_every_ratio_carries_model_identity(self) -> None:
        """I3: an analytic output must say which model produced it."""
        result = net_margin(10_593, 67_535)
        assert result.model_id == "equity.net_margin"
        assert result.model_version
