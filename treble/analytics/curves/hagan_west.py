"""Hagan-West monotone convex interpolation (ADR-0002; spec §11.1.4).

Implements the interpolation of Hagan & West, "Interpolation Methods for
Curve Construction" (Applied Mathematical Finance, 2006), §6: interpolate
the *forward* curve so that

- the curve reproduces every input zero exactly,
- the instantaneous forward is continuous,
- forwards remain positive wherever the discrete forwards are positive,
- no spurious oscillation is introduced (the failure mode of cubic
  splines on zeros — CLAUDE.md §11 failure modes).

Terminology (paper notation): given node times ``t_i`` and zero rates
``r_i``, the *discrete forward* on ``(t_{i-1}, t_i]`` is

    fd_i = (r_i t_i - r_{i-1} t_{i-1}) / (t_i - t_{i-1})

Node forwards ``f_i`` are interpolated from adjacent discrete forwards, then
*ameliorated* (clamped) for positivity; within each interval the forward is
``fd_i + g(x)`` where ``g`` is one of four shapes chosen so that its mean
over the interval is zero — which is exactly what makes node zeros reprice.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass

_EPS = 1e-16  # below this, endpoint offsets are numerically zero (see _g)


def _discrete_forwards(times: list[float], zeros: list[float]) -> list[float]:
    fds = []
    prev_t, prev_rt = 0.0, 0.0
    for t, r in zip(times, zeros, strict=True):
        fds.append((r * t - prev_rt) / (t - prev_t))
        prev_t, prev_rt = t, r * t
    return fds


def _node_forwards(times: list[float], fds: list[float]) -> list[float]:
    """Paper eq. (12)-(14): interpolate f at nodes, then ameliorate for positivity."""
    n = len(times)
    padded = [0.0, *times]
    f = [0.0] * (n + 1)
    for i in range(1, n):
        left_width = padded[i] - padded[i - 1]
        right_width = padded[i + 1] - padded[i]
        f[i] = (left_width / (left_width + right_width)) * fds[i] + (
            right_width / (left_width + right_width)
        ) * fds[i - 1]
    f[0] = fds[0] - 0.5 * (f[1] - fds[0]) if n > 1 else fds[0]
    f[n] = fds[n - 1] - 0.5 * (f[n - 1] - fds[n - 1])
    # Amelioration: enforce positivity-preserving bounds (paper §6.4).
    f[0] = min(max(f[0], 0.0), 2.0 * fds[0])
    for i in range(1, n):
        f[i] = min(max(f[i], 0.0), 2.0 * min(fds[i - 1], fds[i]))
    f[n] = min(max(f[n], 0.0), 2.0 * fds[n - 1])
    return f


def _g(x: float, g0: float, g1: float) -> float:
    """The interval shape function: g(0)=g0, g(1)=g1, integral over [0,1] = 0."""
    if abs(g0) < _EPS and abs(g1) < _EPS:
        return 0.0
    if (
        abs(g0) < _EPS
        or abs(g1) < _EPS
        or (g0 > 0.0 and -0.5 * g0 >= g1 >= -2.0 * g0)
        or (g0 < 0.0 and -0.5 * g0 <= g1 <= -2.0 * g0)
    ):
        # (i) plain quadratic — also the correct continuation when either
        # endpoint offset vanishes (the other regions degenerate there, and
        # their eta terms cancel catastrophically near zero).
        return g0 * (1.0 - 4.0 * x + 3.0 * x * x) + g1 * (-2.0 * x + 3.0 * x * x)
    if (g0 < 0.0 and g1 > -2.0 * g0) or (g0 > 0.0 and g1 < -2.0 * g0):
        # (ii) flat then quadratic
        eta = (g1 + 2.0 * g0) / (g1 - g0)
        if x <= eta:
            return g0
        ratio = (x - eta) / (1.0 - eta)
        return g0 + (g1 - g0) * ratio * ratio
    if (g0 > 0.0 and 0.0 > g1 > -0.5 * g0) or (g0 < 0.0 and 0.0 < g1 < -0.5 * g0):
        # (iii) quadratic then flat
        eta = 3.0 * g1 / (g1 - g0)
        if x < eta:
            ratio = (eta - x) / eta
            return g1 + (g0 - g1) * ratio * ratio
        return g1
    # (iv) same sign: two quadratics meeting at a common level A
    eta = g1 / (g1 + g0)
    a = -g0 * g1 / (g0 + g1)
    if x < eta:
        ratio = (eta - x) / eta
        return a + (g0 - a) * ratio * ratio
    ratio = (x - eta) / (1.0 - eta)
    return a + (g1 - a) * ratio * ratio


def _g_integral(x: float, g0: float, g1: float) -> float:
    """Closed-form integral of g from 0 to x, per shape region."""
    if abs(g0) < _EPS and abs(g1) < _EPS:
        return 0.0
    if (
        abs(g0) < _EPS
        or abs(g1) < _EPS
        or (g0 > 0.0 and -0.5 * g0 >= g1 >= -2.0 * g0)
        or (g0 < 0.0 and -0.5 * g0 <= g1 <= -2.0 * g0)
    ):
        return g0 * (x - 2.0 * x * x + x * x * x) + g1 * (-(x * x) + x * x * x)
    if (g0 < 0.0 and g1 > -2.0 * g0) or (g0 > 0.0 and g1 < -2.0 * g0):
        eta = (g1 + 2.0 * g0) / (g1 - g0)
        if x <= eta:
            return g0 * x
        excess = (x - eta) / (1.0 - eta)
        return g0 * x + (g1 - g0) * (1.0 - eta) * excess * excess * excess / 3.0
    if (g0 > 0.0 and 0.0 > g1 > -0.5 * g0) or (g0 < 0.0 and 0.0 < g1 < -0.5 * g0):
        eta = 3.0 * g1 / (g1 - g0)
        if x < eta:
            ratio = (eta - x) / eta
            return g1 * x + (g0 - g1) * eta * (1.0 - ratio * ratio * ratio) / 3.0
        return g1 * x + (g0 - g1) * eta / 3.0
    eta = g1 / (g1 + g0)
    a = -g0 * g1 / (g0 + g1)
    if x < eta:
        ratio = (eta - x) / eta
        return a * x + (g0 - a) * eta * (1.0 - ratio * ratio * ratio) / 3.0
    excess = (x - eta) / (1.0 - eta)
    return a * x + (g0 - a) * eta / 3.0 + (g1 - a) * (1.0 - eta) * excess * excess * excess / 3.0


@dataclass(frozen=True)
class MonotoneConvex:
    """Interpolator over (time, zero-rate) nodes. Times in years, rates
    continuously compounded. Immutable once constructed."""

    times: tuple[float, ...]
    zeros: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.times) != len(self.zeros) or not self.times:
            raise ValueError("times and zeros must be equal-length and non-empty")
        if any(t2 <= t1 for t1, t2 in zip(self.times, self.times[1:], strict=False)):
            raise ValueError("times must be strictly increasing")
        if self.times[0] <= 0.0:
            raise ValueError("first node time must be positive")

    def _prepared(self) -> tuple[list[float], list[float], list[float], list[float]]:
        times = list(self.times)
        zeros = list(self.zeros)
        fds = _discrete_forwards(times, zeros)
        node_f = _node_forwards(times, fds)
        return times, zeros, fds, node_f

    def forward(self, t: float) -> float:
        """Instantaneous forward rate at t."""
        times, _zeros, fds, node_f = self._prepared()
        if t <= 0.0:
            return node_f[0]
        if t >= times[-1]:
            return node_f[-1]
        i = bisect_left(times, t)
        left = times[i - 1] if i > 0 else 0.0
        x = (t - left) / (times[i] - left)
        g0 = node_f[i] - fds[i]
        g1 = node_f[i + 1] - fds[i]
        return fds[i] + _g(x, g0, g1)

    def zero(self, t: float) -> float:
        """Continuously compounded zero rate at t (nodes reproduced exactly)."""
        times, zeros, fds, node_f = self._prepared()
        if t <= 0.0:
            return node_f[0]
        if t >= times[-1]:
            # Extrapolate flat in the forward beyond the last node.
            last_rt = zeros[-1] * times[-1]
            return (last_rt + node_f[-1] * (t - times[-1])) / t
        i = bisect_left(times, t)
        if times[i] == t:
            return zeros[i]
        left = times[i - 1] if i > 0 else 0.0
        left_rt = zeros[i - 1] * times[i - 1] if i > 0 else 0.0
        width = times[i] - left
        x = (t - left) / width
        g0 = node_f[i] - fds[i]
        g1 = node_f[i + 1] - fds[i]
        rt = left_rt + fds[i] * (t - left) + _g_integral(x, g0, g1) * width
        return rt / t

    def discount(self, t: float) -> float:
        import math

        if t <= 0.0:
            return 1.0
        return math.exp(-self.zero(t) * t)
