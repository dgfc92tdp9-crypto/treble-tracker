"""CMS convexity adjustment (spec §12.1).

The adjustment is the whole content of the module, so the tests are about
its behaviour rather than its value: it must be positive always, vanish
exactly when there is no uncertainty left, and grow the way a second-order
variance term grows. Checking it against a number computed with the same
formula would check nothing.
"""

from __future__ import annotations

import pytest

from treble.analytics.derivatives.cms import _annuity_derivatives, cms_rate

_cms = cms_rate.__wrapped__  # type: ignore[attr-defined]

BASE = {
    "forward_swap_rate": 0.0325,
    "volatility": 0.25,
    "expiry_years": 5.0,
    "tenor_years": 10.0,
}


class TestTheAdjustmentIsPositive:
    @pytest.mark.parametrize("tenor", [2.0, 5.0, 10.0, 30.0])
    @pytest.mark.parametrize("expiry", [1.0, 5.0, 20.0])
    def test_a_cms_rate_never_sits_below_its_forward(self, tenor: float, expiry: float) -> None:
        """The one result that should never appear. G is decreasing and
        convex, so -G''/G' is positive across every tenor and expiry."""
        priced = _cms(**{**BASE, "tenor_years": tenor, "expiry_years": expiry})
        assert priced.rate > priced.forward_swap_rate
        assert priced.adjustment_bp > 0.0


class TestItVanishesWhenItShould:
    def test_no_volatility_means_no_adjustment(self) -> None:
        """Nothing uncertain, nothing to adjust for. It must be exactly
        zero, not merely small."""
        priced = _cms(**{**BASE, "volatility": 0.0})
        assert priced.adjustment_bp == 0.0
        assert priced.rate == priced.forward_swap_rate

    def test_no_time_means_no_adjustment(self) -> None:
        priced = _cms(**{**BASE, "expiry_years": 0.0})
        assert priced.adjustment_bp == 0.0


class TestHowItGrows:
    def test_it_grows_with_the_square_of_volatility(self) -> None:
        """A variance term, not a volatility one. Doubling the vol must
        quadruple the adjustment; a formula linear in vol would double it."""
        single = _cms(**{**BASE, "volatility": 0.20}).adjustment_bp
        double = _cms(**{**BASE, "volatility": 0.40}).adjustment_bp
        assert double == pytest.approx(4.0 * single, rel=1e-9)

    def test_it_grows_linearly_with_time(self) -> None:
        near = _cms(**{**BASE, "expiry_years": 5.0}).adjustment_bp
        far = _cms(**{**BASE, "expiry_years": 15.0}).adjustment_bp
        assert far == pytest.approx(3.0 * near, rel=1e-9)

    def test_a_longer_underlying_swap_convexifies_more(self) -> None:
        """More payments discounted at the swap rate means more curvature in
        the annuity, so a 30-year CMS carries a larger adjustment than a
        2-year one at the same vol."""
        short = _cms(**{**BASE, "tenor_years": 2.0}).adjustment_bp
        long = _cms(**{**BASE, "tenor_years": 30.0}).adjustment_bp
        assert long > short

    def test_the_adjustment_is_material_at_long_tenors(self) -> None:
        """Not a refinement. At 25% vol on a 5-year expiry into 30 years it
        is tens of basis points, which is why pricing a CMS leg at the plain
        forward is wrong rather than approximate."""
        priced = _cms(**{**BASE, "tenor_years": 30.0, "volatility": 0.25})
        assert priced.adjustment_bp > 10.0


class TestItRefusesBadInput:
    def test_a_non_positive_forward_is_refused(self) -> None:
        """Hull's expansion is derived under a lognormal rate and has no
        answer at or below zero; a number here would be invented."""
        with pytest.raises(ValueError, match="lognormal"):
            _cms(**{**BASE, "forward_swap_rate": 0.0})

    def test_a_negative_volatility_is_refused(self) -> None:
        with pytest.raises(ValueError, match="bad input"):
            _cms(**{**BASE, "volatility": -0.1})

    def test_a_negative_expiry_is_refused(self) -> None:
        with pytest.raises(ValueError, match="backwards"):
            _cms(**{**BASE, "expiry_years": -1.0})

    def test_a_zero_tenor_is_refused(self) -> None:
        with pytest.raises(ValueError, match="positive tenor"):
            _cms(**{**BASE, "tenor_years": 0.0})


class TestTheAnnuityDerivatives:
    """`G'` and `G''` pinned against a finite difference of `G` itself.

    Added after the behavioural tests above were shown not to constrain
    them: changing the second derivative's coefficient from i(i+1) to i*i
    left all twenty-two of them passing. Every one checks a property the
    adjustment has — positive, quadratic in vol, linear in time, growing
    with tenor — and a wrong G'' preserves all four while changing the
    number. A finite difference is a genuinely independent computation, so
    it constrains the formula rather than restating it.
    """

    @staticmethod
    def _annuity(rate: float, payments: int, frequency: int) -> float:
        base = 1.0 + rate / frequency
        return sum(base ** (-i) for i in range(1, payments + 1))

    @pytest.mark.parametrize("rate", [0.01, 0.0325, 0.08])
    @pytest.mark.parametrize(("tenor", "frequency"), [(2, 2), (10, 2), (30, 4)])
    def test_they_match_a_finite_difference_of_the_annuity(
        self, rate: float, tenor: int, frequency: int
    ) -> None:
        payments = tenor * frequency
        first, second = _annuity_derivatives(rate, payments, frequency)
        h = 1e-5
        up = self._annuity(rate + h, payments, frequency)
        mid = self._annuity(rate, payments, frequency)
        down = self._annuity(rate - h, payments, frequency)
        assert first == pytest.approx((up - down) / (2 * h), rel=1e-5)
        assert second == pytest.approx((up - 2 * mid + down) / h**2, rel=1e-4)
