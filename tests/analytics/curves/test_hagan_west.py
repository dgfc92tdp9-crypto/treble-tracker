"""Hagan-West monotone convex interpolation (ADR-0002).

Validation strategy: exact analytic cases, the structural properties the
method exists to guarantee (node repricing, forward positivity, forward
continuity), and a numeric cross-check of the closed-form g-integrals
against quadrature — which catches any algebra slip in the region formulas.
"""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from scipy.integrate import quad

from treble.analytics.curves.hagan_west import MonotoneConvex, _g, _g_integral

FLAT = MonotoneConvex(times=(1.0, 2.0, 3.0, 5.0, 10.0), zeros=(0.05,) * 5)
UPWARD = MonotoneConvex(times=(1.0, 2.0, 3.0, 5.0, 10.0), zeros=(0.030, 0.035, 0.040, 0.045, 0.050))
HUMPED = MonotoneConvex(
    times=(0.5, 1.0, 2.0, 5.0, 7.0, 10.0),
    zeros=(0.052, 0.055, 0.050, 0.042, 0.040, 0.041),
)
CURVES = [FLAT, UPWARD, HUMPED]


class TestAnalyticCases:
    def test_flat_curve_flat_forwards(self) -> None:
        for t in (0.25, 1.0, 1.7, 4.2, 9.99):
            assert FLAT.zero(t) == pytest.approx(0.05, abs=1e-14)
            assert FLAT.forward(t) == pytest.approx(0.05, abs=1e-14)

    def test_single_node_is_flat(self) -> None:
        single = MonotoneConvex(times=(2.0,), zeros=(0.04,))
        assert single.zero(1.0) == pytest.approx(0.04, abs=1e-14)
        assert single.forward(0.5) == pytest.approx(0.04, abs=1e-14)

    def test_discount_at_zero_is_one(self) -> None:
        assert FLAT.discount(0.0) == 1.0


class TestStructuralProperties:
    @pytest.mark.parametrize("curve", CURVES)
    def test_nodes_reprice_exactly(self, curve: MonotoneConvex) -> None:
        for t, r in zip(curve.times, curve.zeros, strict=True):
            assert curve.zero(t) == pytest.approx(r, abs=1e-14)

    @pytest.mark.parametrize("curve", CURVES)
    def test_forwards_positive(self, curve: MonotoneConvex) -> None:
        # The property monotone convex exists to guarantee (CLAUDE.md §11):
        # positive discrete forwards => positive instantaneous forwards.
        for i in range(1, 1000):
            t = curve.times[-1] * i / 1000.0
            assert curve.forward(t) >= 0.0, f"negative forward at t={t}"

    @pytest.mark.parametrize("curve", CURVES)
    def test_forward_continuous_at_nodes(self, curve: MonotoneConvex) -> None:
        for t in curve.times[:-1]:
            below, above = curve.forward(t - 1e-9), curve.forward(t + 1e-9)
            assert below == pytest.approx(above, abs=1e-6)

    @pytest.mark.parametrize("curve", CURVES)
    def test_zero_consistent_with_integrated_forward(self, curve: MonotoneConvex) -> None:
        # r(t)·t must equal the integral of the instantaneous forward to t.
        for t in (0.7, 1.5, 3.3, 6.5):
            if t >= curve.times[-1]:
                continue
            integral, _err = quad(curve.forward, 0.0, t, limit=200)
            assert curve.zero(t) * t == pytest.approx(integral, abs=1e-8)

    def test_discount_monotone_decreasing(self) -> None:
        from itertools import pairwise

        for curve in CURVES:
            ts = [curve.times[-1] * i / 200.0 for i in range(1, 201)]
            discounts = [curve.discount(t) for t in ts]
            assert all(a >= b for a, b in pairwise(discounts))


def _region_breakpoints(g0: float, g1: float) -> list[float]:
    """Candidate eta values where the shape function changes formula. Passing
    them to quad stops it stepping over spikes narrower than its sampling
    (Hypothesis found g1 ~ 6e-8 packing the transition into width ~2e-6)."""
    candidates: list[float] = []
    if g1 != g0:
        candidates.append((g1 + 2.0 * g0) / (g1 - g0))
        candidates.append(3.0 * g1 / (g1 - g0))
    if g1 + g0 != 0.0:
        candidates.append(g1 / (g1 + g0))
    return [eta for eta in candidates if 0.0 < eta < 1.0]


@given(
    g0=st.floats(min_value=-0.05, max_value=0.05, allow_nan=False),
    g1=st.floats(min_value=-0.05, max_value=0.05, allow_nan=False),
    x=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
def test_g_integral_matches_quadrature(g0: float, g1: float, x: float) -> None:
    """The closed-form integrals must agree with numeric integration of g."""
    interior = [eta for eta in _region_breakpoints(g0, g1) if eta < x]
    numeric, _err = quad(lambda u: _g(u, g0, g1), 0.0, x, limit=400, points=interior or None)
    assert _g_integral(x, g0, g1) == pytest.approx(numeric, abs=1e-9)


@given(
    g0=st.floats(min_value=-0.05, max_value=0.05, allow_nan=False),
    g1=st.floats(min_value=-0.05, max_value=0.05, allow_nan=False),
)
def test_g_boundary_and_mean_zero(g0: float, g1: float) -> None:
    assert _g(0.0, g0, g1) == pytest.approx(g0, abs=1e-12)
    assert _g(1.0, g0, g1) == pytest.approx(g1, abs=1e-12)
    # Mean-zero over the interval is what makes node zeros reprice.
    assert _g_integral(1.0, g0, g1) == pytest.approx(0.0, abs=1e-12)


class TestValidation:
    def test_rejects_non_increasing_times(self) -> None:
        with pytest.raises(ValueError):
            MonotoneConvex(times=(1.0, 1.0), zeros=(0.05, 0.05))

    def test_rejects_nonpositive_first_time(self) -> None:
        with pytest.raises(ValueError):
            MonotoneConvex(times=(0.0, 1.0), zeros=(0.05, 0.05))

    def test_rejects_length_mismatch(self) -> None:
        with pytest.raises(ValueError):
            MonotoneConvex(times=(1.0, 2.0), zeros=(0.05,))


def test_extrapolation_flat_forward_beyond_last_node() -> None:
    t_last = UPWARD.times[-1]
    f_last = UPWARD.forward(t_last)
    r_beyond = UPWARD.zero(t_last + 2.0)
    expected = (UPWARD.zeros[-1] * t_last + f_last * 2.0) / (t_last + 2.0)
    assert r_beyond == pytest.approx(expected, abs=1e-14)
    assert not math.isnan(UPWARD.discount(t_last + 5.0))
