"""The amelioration step — what makes Hagan-West *monotone convex*.

Found by `scripts/mutation_check.py`: deleting either positivity clamp, or
flipping the terminal extrapolation sign, left the entire curve suite green.
The tests asserted that a curve reprices its inputs, which it does with or
without amelioration for well-behaved data — so the property the algorithm
is *named for* was never checked.

A surviving mutant here means a wrong interest rate, not a wrong log line.

These assert properties rather than reconstructing internals: the check that
they actually bite is `scripts/mutation_check.py`, which deletes each clamp
and confirms the suite goes red. A test that guesses at intermediate values
would break whenever the implementation was refactored without the property
having changed at all.
"""

from __future__ import annotations

import pytest

from treble.analytics.curves.hagan_west import _discrete_forwards, _node_forwards


def _nodes(times: list[float], zeros: list[float]) -> tuple[list[float], list[float]]:
    fds = _discrete_forwards(times, zeros)
    return fds, _node_forwards(times, fds)


class TestPositivity:
    """Hagan-West §4: node forwards are clamped so a curve built from
    positive forwards stays positive.

    A steep *rise* at the short end drives the extrapolated first node
    negative without the clamp, and a negative instantaneous forward on
    positive data is not a rate anyone can quote.
    """

    #: Steeply rising short end. Every discrete forward stays positive, so
    #: positivity is a property the data supports and the clamp must deliver.
    RISING = (0.001, 0.0505, 0.040, 0.035)
    TIMES = (0.5, 1.0, 2.0, 5.0)

    def test_every_node_forward_is_non_negative(self) -> None:
        fds, f = _nodes(list(self.TIMES), list(self.RISING))
        assert all(value >= 0.0 for value in fds), "premise: inputs must be positive"
        assert all(value >= 0.0 for value in f), f"negative node forward in {f}"

    def test_negative_forwards_are_not_forced_positive(self) -> None:
        """The clamp preserves the sign the data implies rather than
        inventing positivity. Steeply falling zeros give a genuinely negative
        discrete forward — real in several markets since 2014 — and a curve
        that reported it as zero would be asserting something the filing
        never said."""
        times, falling = [0.5, 1.0, 2.0, 5.0], [0.050, 0.010, 0.009, 0.008]
        fds = _discrete_forwards(times, falling)
        assert min(fds) < 0.0
        assert min(_node_forwards(times, fds)) < 0.0


class TestAmelioration:
    """The upper bound is what keeps the interpolant monotone: a node may not
    exceed twice the smaller of its neighbouring discrete forwards."""

    @pytest.mark.parametrize(
        "zeros",
        [
            [0.010, 0.055, 0.020, 0.021],
            [0.020, 0.021, 0.080, 0.022],
            [0.030, 0.031, 0.032, 0.090],
        ],
    )
    def test_interior_nodes_respect_the_upper_bound(self, zeros: list[float]) -> None:
        times = [0.5, 1.0, 2.0, 5.0]
        fds, f = _nodes(times, zeros)
        for i in range(1, len(fds)):
            assert f[i] <= 2.0 * min(fds[i - 1], fds[i]) + 1e-12, f"node {i} exceeds its bound"

    def test_the_first_node_respects_its_bound(
        self,
    ) -> None:
        fds, f = _nodes([0.5, 1.0, 2.0, 5.0], [0.010, 0.055, 0.020, 0.021])
        assert f[0] <= 2.0 * fds[0] + 1e-12

    def test_the_last_node_respects_its_bound(self) -> None:
        fds, f = _nodes([0.5, 1.0, 2.0, 5.0], [0.030, 0.031, 0.032, 0.090])
        assert f[-1] <= 2.0 * fds[-1] + 1e-12


class TestTerminalExtrapolation:
    """The last node is extrapolated *away* from its neighbour, not toward
    it. A flipped sign bends the long end the wrong way — and every discount
    factor beyond the final input tenor with it."""

    def test_the_long_end_extrapolates_in_the_right_direction(self) -> None:
        times, zeros = [0.5, 1.0, 2.0, 5.0], [0.020, 0.022, 0.025, 0.030]
        fds, f = _nodes(times, zeros)
        expected = fds[-1] - 0.5 * (f[-2] - fds[-1])
        clamped = min(max(expected, 0.0), 2.0 * fds[-1])
        assert f[-1] == pytest.approx(clamped)

    def test_a_rising_curve_puts_the_last_node_above_the_last_forward(self) -> None:
        """On a curve whose forwards rise, the terminal node must sit above
        the final discrete forward. The sign flip puts it below."""
        times, zeros = [0.5, 1.0, 2.0, 5.0], [0.020, 0.022, 0.025, 0.030]
        fds, f = _nodes(times, zeros)
        assert f[-1] > fds[-1], "terminal node fell below the final forward"
