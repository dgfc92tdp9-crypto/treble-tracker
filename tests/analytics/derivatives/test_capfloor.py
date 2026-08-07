"""Caps and floors (spec §12.1).

Put-call parity is the headline check here, and writing it taught me its
limit. `cap - floor` must equal the value of paying the strike on the same
strip, for any vol and both conventions — but the annuity multiplies the
caplet values and the strike leg alike, so it **cancels**. Hard-coding
`accrual` to a constant leaves all of these passing, including the irregular
schedule below that exists to catch precisely that. I mutated the code to
check, and 27 of 27 stayed green.

`TestTheAnnuityIsWhatItClaims` is the answer: the annuity is pinned to
`accrual * discount_factor` directly, and the absolute level is anchored to
the closed-form ATM Bachelier price computed here rather than through the
pricer. Those fail under the mutation. Parity sits alongside them.
"""

from __future__ import annotations

import math

import pytest

from treble.analytics.derivatives.capfloor import Caplet, price_cap, price_floor

_cap = price_cap.__wrapped__  # type: ignore[attr-defined]
_floor = price_floor.__wrapped__  # type: ignore[attr-defined]

FORWARD = 0.0325
RATE = 0.03


def _strip(periods: int = 8, forward: float = FORWARD) -> list[Caplet]:
    """A quarterly strip out to `periods`, discounted at a flat 3%."""
    out = []
    for n in range(periods):
        start = 0.25 * (n + 1)
        pay = start + 0.25
        out.append(
            Caplet(
                forward=forward,
                accrual=0.25,
                discount_factor=math.exp(-RATE * pay),
                expiry_years=start,
            )
        )
    return out


class TestParity:
    """cap - floor = value of paying the strike on the same schedule."""

    @pytest.mark.parametrize("strike", [0.02, 0.0325, 0.045])
    @pytest.mark.parametrize("volatility", [0.0001, 0.004, 0.02])
    def test_parity_holds_under_normal_vol(self, strike: float, volatility: float) -> None:
        cap = _cap(_strip(), strike=strike, volatility=volatility, normal_vol=True)
        floor = _floor(_strip(), strike=strike, volatility=volatility, normal_vol=True)
        assert cap.value - floor.value == pytest.approx(cap.strike_annuity_value, abs=1e-12)

    @pytest.mark.parametrize("strike", [0.02, 0.0325, 0.045])
    @pytest.mark.parametrize("volatility", [0.05, 0.20, 0.60])
    def test_parity_holds_under_lognormal_vol(self, strike: float, volatility: float) -> None:
        cap = _cap(_strip(), strike=strike, volatility=volatility, normal_vol=False)
        floor = _floor(_strip(), strike=strike, volatility=volatility, normal_vol=False)
        assert cap.value - floor.value == pytest.approx(cap.strike_annuity_value, abs=1e-12)

    def test_parity_survives_an_irregular_schedule(self) -> None:
        """Parity must hold when the periods are not uniform.

        Written believing uneven accruals would catch a wrong one where
        equal accruals could not. They do not: the annuity is a common
        factor on both sides of parity, so it cancels whatever its value,
        and hard-coding it left this passing too. Kept because it does check
        that no period is dropped or mis-paired on an irregular schedule —
        which is a real property, just not the one it was written for.
        """
        strip = [
            Caplet(forward=0.031, accrual=0.5, discount_factor=0.985, expiry_years=0.5),
            Caplet(forward=0.034, accrual=0.25, discount_factor=0.977, expiry_years=1.0),
            Caplet(forward=0.036, accrual=0.75, discount_factor=0.955, expiry_years=1.25),
        ]
        cap = _cap(strip, strike=0.033, volatility=0.005, normal_vol=True)
        floor = _floor(strip, strike=0.033, volatility=0.005, normal_vol=True)
        assert cap.value - floor.value == pytest.approx(cap.strike_annuity_value, abs=1e-12)


class TestTheStripIsVisible:
    def test_every_caplet_is_reported_not_just_the_total(self) -> None:
        """A cap is a strip, and the strip is where a wrong period hides. One
        number cannot show that a single caplet carries the whole price."""
        cap = _cap(_strip(6), strike=0.0325, volatility=0.004, normal_vol=True)
        assert len(cap.caplets) == 6
        assert sum(cap.caplets) == pytest.approx(cap.value)

    def test_the_convention_travels_with_the_price(self) -> None:
        """90 basis points and 90 percent render identically."""
        assert _cap(_strip(), strike=0.0325, volatility=0.004, normal_vol=True).normal_vol
        assert not _cap(_strip(), strike=0.0325, volatility=0.2, normal_vol=False).normal_vol


class TestBehaviour:
    def test_a_cap_is_worth_more_when_vol_is_higher(self) -> None:
        low = _cap(_strip(), strike=0.0325, volatility=0.002, normal_vol=True).value
        high = _cap(_strip(), strike=0.0325, volatility=0.008, normal_vol=True).value
        assert high > low

    def test_a_higher_strike_cap_is_cheaper(self) -> None:
        cheap = _cap(_strip(), strike=0.045, volatility=0.004, normal_vol=True).value
        dear = _cap(_strip(), strike=0.020, volatility=0.004, normal_vol=True).value
        assert dear > cheap

    def test_a_fixed_period_settles_for_intrinsic(self) -> None:
        """A caplet whose fixing has passed has no time value left. Pricing
        it with `expiry_years <= 0` would divide by a zero total vol."""
        fixed = [Caplet(forward=0.04, accrual=0.25, discount_factor=0.99, expiry_years=0.0)]
        cap = _cap(fixed, strike=0.03, volatility=0.004, normal_vol=True)
        assert cap.value == pytest.approx(0.25 * 0.99 * 0.01)
        floor = _floor(fixed, strike=0.03, volatility=0.004, normal_vol=True)
        assert floor.value == pytest.approx(0.0)

    def test_a_normal_vol_cap_prices_through_a_negative_forward(self) -> None:
        """The reason Bachelier is the default. Black has no answer below
        zero rather than a large one, and EUR caps trade there."""
        negative = [Caplet(forward=-0.002, accrual=0.25, discount_factor=0.999, expiry_years=1.0)]
        value = _cap(negative, strike=-0.004, volatility=0.004, normal_vol=True).value
        assert value > 0.0


class TestItRefusesBadInput:
    def test_an_empty_schedule_is_refused(self) -> None:
        """An empty schedule and a worthless cap render the same and are not
        the same."""
        with pytest.raises(ValueError, match="no periods"):
            _cap([], strike=0.03, volatility=0.004, normal_vol=True)

    def test_a_negative_volatility_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a cheap option"):
            _cap(_strip(), strike=0.03, volatility=-0.001, normal_vol=True)


class TestTheAnnuityIsWhatItClaims:
    """The checks parity cannot make, because the annuity cancels in it."""

    @pytest.mark.parametrize(
        ("accrual", "discount_factor"),
        [(0.25, 0.99), (0.5, 0.985), (0.75, 0.955), (1.0, 0.97)],
    )
    def test_the_annuity_is_accrual_times_discount_factor(
        self, accrual: float, discount_factor: float
    ) -> None:
        caplet = Caplet(
            forward=FORWARD,
            accrual=accrual,
            discount_factor=discount_factor,
            expiry_years=1.0,
        )
        assert caplet.annuity == pytest.approx(accrual * discount_factor)

    def test_an_atm_caplet_matches_the_closed_form(self) -> None:
        """Bachelier at the money is `annuity * vol * sqrt(T) / sqrt(2*pi)`.

        Computed here rather than by calling the pricer, so it anchors the
        absolute level rather than restating whatever the pricer does. This
        is the test that fails when the annuity is wrong.
        """
        accrual, discount_factor, vol, expiry = 0.5, 0.985, 0.004, 2.0
        caplet = Caplet(
            forward=FORWARD,
            accrual=accrual,
            discount_factor=discount_factor,
            expiry_years=expiry,
        )
        expected = accrual * discount_factor * vol * math.sqrt(expiry) / math.sqrt(2.0 * math.pi)
        priced = _cap([caplet], strike=FORWARD, volatility=vol, normal_vol=True)
        assert priced.value == pytest.approx(expected, rel=1e-12)
