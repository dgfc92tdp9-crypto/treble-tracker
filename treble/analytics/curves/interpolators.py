"""Interpolator implementations behind the Interpolation enum (spec §11.1.4).

Each maps (node times, node zeros) -> a continuous zero/discount curve.
The bootstrap is generic over this protocol, which is what lets the in-repo
Hagan-West method participate identically to the standard methods
(ADR-0002). Natural/monotonic cubics use SciPy (stack §2); the log-linear
discount method is cross-validated against QuantLib in the golden tests.
"""

from __future__ import annotations

import math
from typing import Protocol

import numpy as np
from scipy.interpolate import CubicSpline, PchipInterpolator

from treble.analytics.curves.config import Interpolation
from treble.analytics.curves.hagan_west import MonotoneConvex


class Interpolator(Protocol):
    def zero(self, t: float) -> float: ...
    def discount(self, t: float) -> float: ...


class _ZeroBased:
    """Base for methods interpolating on the zero-rate axis."""

    def __init__(self, times: tuple[float, ...], zeros: tuple[float, ...]) -> None:
        self._times = times
        self._zeros = zeros

    def _interp_zero(self, t: float) -> float:
        raise NotImplementedError

    def zero(self, t: float) -> float:
        if t <= self._times[0]:
            return self._zeros[0]
        if t >= self._times[-1]:
            return self._zeros[-1]  # flat zero extrapolation
        return self._interp_zero(t)

    def discount(self, t: float) -> float:
        if t <= 0.0:
            return 1.0
        return math.exp(-self.zero(t) * t)


class LinearZero(_ZeroBased):
    def _interp_zero(self, t: float) -> float:
        return float(np.interp(t, self._times, self._zeros))


class NaturalCubicZero(_ZeroBased):
    def __init__(self, times: tuple[float, ...], zeros: tuple[float, ...]) -> None:
        super().__init__(times, zeros)
        if len(times) >= 2:
            self._spline = CubicSpline(times, zeros, bc_type="natural")

    def _interp_zero(self, t: float) -> float:
        return float(self._spline(t))


class MonotonicCubicZero(_ZeroBased):
    def __init__(self, times: tuple[float, ...], zeros: tuple[float, ...]) -> None:
        super().__init__(times, zeros)
        if len(times) >= 2:
            self._spline = PchipInterpolator(times, zeros)

    def _interp_zero(self, t: float) -> float:
        return float(self._spline(t))


class LogLinearDiscount:
    """Linear in log-discount == piecewise-constant forwards."""

    def __init__(self, times: tuple[float, ...], zeros: tuple[float, ...]) -> None:
        self._times = (0.0, *times)
        self._log_dfs = (0.0, *(-z * t for z, t in zip(zeros, times, strict=True)))

    def _log_df(self, t: float) -> float:
        if t <= 0.0:
            return 0.0
        if t >= self._times[-1]:
            # flat forward extrapolation: extend the last segment's slope
            slope = (self._log_dfs[-1] - self._log_dfs[-2]) / (self._times[-1] - self._times[-2])
            return self._log_dfs[-1] + slope * (t - self._times[-1])
        return float(np.interp(t, self._times, self._log_dfs))

    def discount(self, t: float) -> float:
        return math.exp(self._log_df(t))

    def zero(self, t: float) -> float:
        if t <= 0.0:
            t = min(x for x in self._times if x > 0.0)
        return -self._log_df(t) / t


class MonotoneConvexAdapter:
    def __init__(self, times: tuple[float, ...], zeros: tuple[float, ...]) -> None:
        self._mc = MonotoneConvex(times=times, zeros=zeros)

    def zero(self, t: float) -> float:
        return self._mc.zero(t)

    def discount(self, t: float) -> float:
        return self._mc.discount(t)


def make_interpolator(
    method: Interpolation, times: tuple[float, ...], zeros: tuple[float, ...]
) -> Interpolator:
    match method:
        case Interpolation.LINEAR_ZERO:
            return LinearZero(times, zeros)
        case Interpolation.LOGLINEAR_DISCOUNT:
            return LogLinearDiscount(times, zeros)
        case Interpolation.NATURAL_CUBIC_ZERO:
            return NaturalCubicZero(times, zeros)
        case Interpolation.MONOTONIC_CUBIC_ZERO:
            return MonotonicCubicZero(times, zeros)
        case Interpolation.MONOTONE_CONVEX:
            return MonotoneConvexAdapter(times, zeros)
