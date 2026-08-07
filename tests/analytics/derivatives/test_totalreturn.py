"""Total return swaps (spec §12.1).

Three things here can be wrong in a way that still looks like a number: the
income term being dropped, financing accruing on the current mark instead of
the notional, and the payer/receiver sign. Each has a test that fails when
it is changed, checked by changing it.
"""

from __future__ import annotations

import pytest

from treble.analytics.derivatives.totalreturn import total_return_swap

_trs = total_return_swap.__wrapped__  # type: ignore[attr-defined]

BASE = {
    "notional": 10_000_000.0,
    "reset_price": 100.0,
    "current_price": 100.0,
    "income": 0.0,
    "funding_rate": 0.032,
    "spread": 0.005,
    "accrual": 0.0,
    "discount_factor": 1.0,
}


class TestAtTheReset:
    def test_a_swap_at_its_reset_is_worth_nothing(self) -> None:
        """No price move, no income, no time elapsed. Anything other than
        zero here means a leg is accruing when it should not be."""
        priced = _trs(**BASE)
        assert priced.value == pytest.approx(0.0)
        assert priced.return_leg == pytest.approx(0.0)
        assert priced.funding_leg == pytest.approx(0.0)


class TestTheLegsAreSeparate:
    def test_a_price_gain_accrues_to_the_return_receiver(self) -> None:
        priced = _trs(**{**BASE, "current_price": 108.0})
        assert priced.price_return == pytest.approx(800_000.0)
        assert priced.value == pytest.approx(800_000.0)

    def test_income_is_counted_and_kept_apart_from_price(self) -> None:
        """A total return leg pays dividends as well as appreciation.
        Dropping the term understates it by exactly the income, and most for
        the high-yielding assets people use these structures to hold."""
        priced = _trs(**{**BASE, "current_price": 108.0, "income": 2.0})
        assert priced.price_return == pytest.approx(800_000.0)
        assert priced.income_return == pytest.approx(200_000.0)
        assert priced.value == pytest.approx(1_000_000.0)

    def test_both_legs_are_reported_not_only_the_net(self) -> None:
        """+8% against 3.7% financing nets to something that looks like a
        small position rather than a large one held against a liability."""
        priced = _trs(**{**BASE, "current_price": 108.0, "accrual": 1.0})
        assert priced.return_leg == pytest.approx(800_000.0)
        assert priced.funding_leg == pytest.approx(370_000.0)
        assert priced.value == pytest.approx(430_000.0)


class TestFinancing:
    def test_financing_accrues_on_the_notional_not_the_mark(self) -> None:
        """An unfunded TRS finances what was borrowed, whatever the asset
        has since done. Accruing on the mark is a plausible error that grows
        with the position's gain -- so this doubles the price and asserts
        the funding leg does not move."""
        flat = _trs(**{**BASE, "current_price": 100.0, "accrual": 1.0})
        doubled = _trs(**{**BASE, "current_price": 200.0, "accrual": 1.0})
        assert doubled.funding_leg == pytest.approx(flat.funding_leg)
        assert doubled.funding_leg == pytest.approx(370_000.0)

    def test_a_wider_spread_costs_the_return_receiver(self) -> None:
        tight = _trs(**{**BASE, "accrual": 1.0, "spread": 0.001}).value
        wide = _trs(**{**BASE, "accrual": 1.0, "spread": 0.010}).value
        assert wide < tight

    def test_discounting_scales_both_legs_together(self) -> None:
        undiscounted = _trs(**{**BASE, "current_price": 108.0, "accrual": 1.0})
        discounted = _trs(
            **{**BASE, "current_price": 108.0, "accrual": 1.0, "discount_factor": 0.5}
        )
        assert discounted.value == pytest.approx(undiscounted.value * 0.5)
        assert discounted.funding_leg == pytest.approx(undiscounted.funding_leg * 0.5)


class TestTheSign:
    def test_the_payer_of_the_return_has_the_opposite_value(self) -> None:
        receiver = _trs(**{**BASE, "current_price": 108.0, "accrual": 1.0})
        payer = _trs(**{**BASE, "current_price": 108.0, "accrual": 1.0}, receive_return=False)
        assert payer.value == pytest.approx(-receiver.value)
        assert payer.value < 0.0


class TestItRefusesBadInput:
    def test_a_zero_reset_price_is_refused(self) -> None:
        """No base for the return. Dividing by it would give infinity, which
        renders as an enormous position rather than as the error it is."""
        with pytest.raises(ValueError, match="no base for the return"):
            _trs(**{**BASE, "reset_price": 0.0})

    def test_a_non_positive_notional_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a position"):
            _trs(**{**BASE, "notional": 0.0})

    def test_a_negative_accrual_is_refused(self) -> None:
        with pytest.raises(ValueError, match="backwards in time"):
            _trs(**{**BASE, "accrual": -0.25})
