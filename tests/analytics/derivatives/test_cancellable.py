"""Cancellable and extendible swaps (spec §12.1).

The check that matters is the sign. A cancellable swap must be worth *less*
than the vanilla it is built from — the holder is long an option and pays
for it — and getting that backwards produces a number that looks entirely
plausible on a screen. Everything else here is arithmetic the swaption
pricers already own.
"""

from __future__ import annotations

import pytest

from treble.analytics.derivatives.cancellable import cancellable_swap, extendible_swap
from treble.analytics.vol.swaption import bachelier_swaption

_cancel = cancellable_swap.__wrapped__  # type: ignore[attr-defined]
_extend = extendible_swap.__wrapped__  # type: ignore[attr-defined]

BASE = {
    "forward": 0.0325,
    "strike": 0.0325,
    "expiry_years": 3.0,
    "volatility": 0.005,
    "annuity": 6.0,
}


class TestTheSignIsRight:
    @pytest.mark.parametrize("payer", [True, False])
    def test_a_cancellable_is_worth_less_than_its_vanilla(self, payer: bool) -> None:
        """The holder is long the cancellation right and pays for it. A
        cancellable worth more than the swap it is built from is the one
        error here that would look plausible."""
        priced = _cancel(vanilla_value=100_000.0, payer=payer, **BASE)
        assert priced.value < priced.vanilla_value
        assert priced.option_value > 0.0

    @pytest.mark.parametrize("payer", [True, False])
    def test_an_extendible_is_worth_more_than_its_short_vanilla(self, payer: bool) -> None:
        """The underlying stops short, so the option is bought on top rather
        than given up."""
        priced = _extend(vanilla_value=100_000.0, payer=payer, **BASE)
        assert priced.value > priced.vanilla_value
        assert priced.option_value > 0.0

    @pytest.mark.parametrize("payer", [True, False])
    def test_the_embedded_option_is_the_offsetting_one(self, payer: bool) -> None:
        """Cancelling a payer means entering the receiver, so the embedded
        option is a receiver swaption — and vice versa.

        Pinned against the swaption pricer directly rather than by comparing
        the two cases to each other. The first version asserted only that a
        cancellable payer and a cancellable receiver embed options of
        *different* value, which stays true when the two are swapped: I
        flipped `payer=not payer` in the source and all twelve tests passed.
        A check that survives the mutation it was written for is not a check.
        """
        away = {**BASE, "strike": 0.045}
        expected = bachelier_swaption.__wrapped__(  # type: ignore[attr-defined]
            forward=away["forward"],
            strike=away["strike"],
            expiry_years=away["expiry_years"],
            volatility=away["volatility"],
            annuity=away["annuity"],
            payer=not payer,
        )
        priced = _cancel(vanilla_value=0.0, payer=payer, **away)
        assert priced.option_value == pytest.approx(expected)


class TestTheDecompositionIsVisible:
    def test_the_parts_reconstruct_the_total(self) -> None:
        priced = _cancel(vanilla_value=100_000.0, **BASE)
        assert priced.vanilla_value - priced.option_value == pytest.approx(priced.value)

    def test_option_share_says_how_much_is_optionality(self) -> None:
        priced = _cancel(vanilla_value=100_000.0, **BASE)
        assert 0.0 < priced.option_share < 1.0

    def test_a_worthless_option_leaves_the_vanilla_alone(self) -> None:
        """At zero vol and at the money the right is worth nothing, and the
        structure must collapse to the swap rather than to something near
        it."""
        priced = _cancel(vanilla_value=100_000.0, **{**BASE, "volatility": 0.0})
        assert priced.option_value == pytest.approx(0.0)
        assert priced.value == pytest.approx(100_000.0)

    def test_more_vol_makes_the_cancellable_cheaper(self) -> None:
        low = _cancel(vanilla_value=100_000.0, **{**BASE, "volatility": 0.002}).value
        high = _cancel(vanilla_value=100_000.0, **{**BASE, "volatility": 0.010}).value
        assert high < low

    def test_the_convention_travels_with_the_price(self) -> None:
        assert _cancel(vanilla_value=0.0, normal_vol=True, **BASE).normal_vol
        assert not _cancel(
            vanilla_value=0.0, normal_vol=False, **{**BASE, "volatility": 0.3}
        ).normal_vol


class TestItRefusesBadInput:
    def test_an_expired_right_is_refused(self) -> None:
        """No decision left to make. Pricing it as an option would divide by
        a zero total vol; returning the vanilla silently would hide that the
        caller passed a date in the past."""
        with pytest.raises(ValueError, match="nothing left to decide"):
            _cancel(vanilla_value=0.0, **{**BASE, "expiry_years": 0.0})

    def test_a_negative_volatility_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a cheap option"):
            _cancel(vanilla_value=0.0, **{**BASE, "volatility": -0.001})
