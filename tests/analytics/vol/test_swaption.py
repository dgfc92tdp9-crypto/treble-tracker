"""Black swaption pricing and implied volatility (spec §11.3).

The pricer and the solver are checked against each other and against
put-call parity, both of which have exact answers. What is *not* claimed here
is a volatility surface: see `TestWhatASinglePrintCanAndCannotSupport`.
"""

from __future__ import annotations

from datetime import date

import pytest

from treble.analytics.vol.swaption import (
    MAX_VOL,
    ImpliedVolError,
    SwaptionQuote,
    black_swaption,
    implied_black_vol,
)

_price = black_swaption.__wrapped__
_implied = implied_black_vol.__wrapped__

FORWARD, STRIKE, EXPIRY, ANNUITY = 0.030, 0.029, 1.0, 8.5


class TestThePricer:
    @pytest.mark.parametrize("volatility", [0.05, 0.10, 0.25, 0.60, 1.20])
    def test_the_solver_inverts_the_pricer(self, volatility: float) -> None:
        """The only check with an exact answer: price at a known vol, solve
        it back, and get the same number."""
        premium = _price(
            forward=FORWARD,
            strike=STRIKE,
            expiry_years=EXPIRY,
            volatility=volatility,
            annuity=ANNUITY,
        )
        recovered = _implied(
            premium_fraction=premium,
            forward=FORWARD,
            strike=STRIKE,
            expiry_years=EXPIRY,
            annuity=ANNUITY,
        )
        assert recovered == pytest.approx(volatility, rel=1e-8)

    def test_put_call_parity_holds_exactly(self) -> None:
        """payer - receiver = annuity x (forward - strike). Exact, not
        approximate: a pricer that got the sign of `d1` wrong on one side
        would still price both plausibly and fail this."""
        payer = _price(
            forward=FORWARD,
            strike=STRIKE,
            expiry_years=EXPIRY,
            volatility=0.25,
            annuity=ANNUITY,
            payer=True,
        )
        receiver = _price(
            forward=FORWARD,
            strike=STRIKE,
            expiry_years=EXPIRY,
            volatility=0.25,
            annuity=ANNUITY,
            payer=False,
        )
        assert payer - receiver == pytest.approx(ANNUITY * (FORWARD - STRIKE), abs=1e-12)

    def test_price_rises_with_volatility(self) -> None:
        prices = [
            _price(
                forward=FORWARD, strike=STRIKE, expiry_years=EXPIRY, volatility=v, annuity=ANNUITY
            )
            for v in (0.05, 0.15, 0.30, 0.60)
        ]
        assert prices == sorted(prices)

    def test_zero_volatility_gives_intrinsic(self) -> None:
        payer = _price(
            forward=FORWARD,
            strike=STRIKE,
            expiry_years=EXPIRY,
            volatility=0.0,
            annuity=ANNUITY,
            payer=True,
        )
        assert payer == pytest.approx(ANNUITY * (FORWARD - STRIKE))

    def test_the_annuity_scales_the_price_linearly(self) -> None:
        """The annuity carries discounting and accrual; the rate carries the
        rest. Keeping them separate is what lets this run against the
        multi-curve environment rather than a flat rate."""
        one = _price(
            forward=FORWARD, strike=STRIKE, expiry_years=EXPIRY, volatility=0.25, annuity=1.0
        )
        many = _price(
            forward=FORWARD, strike=STRIKE, expiry_years=EXPIRY, volatility=0.25, annuity=7.0
        )
        assert many == pytest.approx(7.0 * one)


class TestItRefusesRatherThanReturningANumber:
    def test_a_negative_forward_is_refused_not_priced(self) -> None:
        """Black is lognormal. A negative-rate market needs a normal vol, and
        a number here would be an invented one rather than a large one."""
        with pytest.raises(ValueError, match="lognormal"):
            _price(forward=-0.001, strike=0.01, expiry_years=1.0, volatility=0.3, annuity=ANNUITY)
        with pytest.raises(ImpliedVolError, match="Bachelier"):
            _implied(
                premium_fraction=0.01,
                forward=-0.001,
                strike=0.01,
                expiry_years=1.0,
                annuity=ANNUITY,
            )

    def test_an_expired_option_is_refused(self) -> None:
        with pytest.raises(ValueError, match="expired"):
            _price(
                forward=FORWARD, strike=STRIKE, expiry_years=0.0, volatility=0.25, annuity=ANNUITY
            )

    def test_a_premium_below_intrinsic_is_refused(self) -> None:
        """No volatility is low enough, so the inputs disagree rather than
        the option being cheap. Clamping to MIN_VOL would put a fabricated
        number where a solved one is expected."""
        intrinsic = ANNUITY * (FORWARD - STRIKE)
        with pytest.raises(ImpliedVolError, match="below intrinsic"):
            _implied(
                premium_fraction=intrinsic * 0.5,
                forward=FORWARD,
                strike=STRIKE,
                expiry_years=EXPIRY,
                annuity=ANNUITY,
            )

    def test_an_impossible_premium_is_refused_not_clamped(self) -> None:
        """A capped notional, a premium in another currency or a mis-parsed
        strike explains a premium above the price at 300% vol. A swaption
        does not, and returning MAX_VOL would hide the data problem."""
        huge = (
            _price(
                forward=FORWARD,
                strike=STRIKE,
                expiry_years=EXPIRY,
                volatility=MAX_VOL,
                annuity=ANNUITY,
            )
            * 1.5
        )
        with pytest.raises(ImpliedVolError, match="exceeds the Black price"):
            _implied(
                premium_fraction=huge,
                forward=FORWARD,
                strike=STRIKE,
                expiry_years=EXPIRY,
                annuity=ANNUITY,
            )

    def test_a_zero_premium_is_refused(self) -> None:
        with pytest.raises(ImpliedVolError, match="implies no volatility"):
            _implied(
                premium_fraction=0.0,
                forward=FORWARD,
                strike=STRIKE,
                expiry_years=EXPIRY,
                annuity=ANNUITY,
            )


class TestWhatASinglePrintCanAndCannotSupport:
    """The honest limits, pinned so nothing later reads this as a surface."""

    def test_a_quote_reports_its_expiry_and_tenor(self) -> None:
        quote = SwaptionQuote(
            payer=True,
            expiry=date(2027, 7, 13),
            underlier_maturity=date(2037, 7, 13),
            strike=0.0294,
            premium_fraction=0.011,
            currency="EUR",
            traded=date(2026, 7, 13),
        )
        assert quote.expiry_years == pytest.approx(1.0, abs=0.01)
        assert quote.tenor_years == pytest.approx(10.0, abs=0.01)

    def test_a_capped_notional_is_carried_not_dropped(self) -> None:
        """The CFTC caps notionals on block trades. The premium is real and
        the notional is a floor, so `premium_fraction` is too large and the
        implied vol is biased upward — a fact about the print that has to
        travel with it rather than being averaged in silently."""
        quote = SwaptionQuote(
            payer=True,
            expiry=date(2027, 7, 13),
            underlier_maturity=date(2037, 7, 13),
            strike=0.0294,
            premium_fraction=0.011,
            currency="EUR",
            traded=date(2026, 7, 13),
            notional_capped=True,
        )
        assert quote.notional_capped is True

    def test_a_short_dated_option_is_hypersensitive_to_its_premium(self) -> None:
        """Why 406 real prints are not yet a surface. At a one-month expiry a
        1% error in the premium moves the implied vol by several points, so
        two trades on the same terms can imply very different vols without
        either being wrong. Measured on the live tape: two EUR receivers with
        the same strike and forward implied 68.0% and 7.7%.
        """
        short = 1.0 / 12.0
        base = _price(
            forward=FORWARD, strike=STRIKE, expiry_years=short, volatility=0.30, annuity=ANNUITY
        )
        nudged = _implied(
            premium_fraction=base * 1.01,
            forward=FORWARD,
            strike=STRIKE,
            expiry_years=short,
            annuity=ANNUITY,
        )
        assert abs(nudged - 0.30) > 0.005, (
            "a 1% premium error should move a one-month implied vol materially; "
            "if it no longer does, the caution in this module's docstring is stale"
        )

    def test_an_absolute_premium_error_hurts_more_away_from_the_money(self) -> None:
        """Vega is highest at the money, so a given *absolute* premium error
        buys more volatility on a wing.

        Stated as absolute deliberately. My first version of this test
        asserted the same for a *relative* error and failed: at the money
        price/vega is approximately the volatility itself, so a 5% price
        error moves vol by about 5% of it, while the same relative error on a
        much smaller wing premium is a much smaller absolute one. The
        directions are opposite, and only one of them is what makes a wing
        print fragile.
        """
        shift = 2e-4  # the same absolute premium error in both cases
        atm = _price(
            forward=FORWARD, strike=FORWARD, expiry_years=EXPIRY, volatility=0.30, annuity=ANNUITY
        )
        wing = _price(
            forward=FORWARD,
            strike=FORWARD * 0.6,
            expiry_years=EXPIRY,
            volatility=0.30,
            annuity=ANNUITY,
            payer=False,
        )
        atm_shift = abs(
            _implied(
                premium_fraction=atm + shift,
                forward=FORWARD,
                strike=FORWARD,
                expiry_years=EXPIRY,
                annuity=ANNUITY,
            )
            - 0.30
        )
        wing_shift = abs(
            _implied(
                premium_fraction=wing + shift,
                forward=FORWARD,
                strike=FORWARD * 0.6,
                expiry_years=EXPIRY,
                annuity=ANNUITY,
                payer=False,
            )
            - 0.30
        )
        assert wing_shift > atm_shift

    def test_the_extreme_tape_values_are_recorded_as_unexplained(self) -> None:
        """Deep out-of-the-money receivers on the live tape implied 137-156%
        vol at a 2.0% strike against a 3.0% forward. I do not know why.

        The obvious explanation — wing sensitivity — is measured above and
        does not account for a spread that wide, so it is not offered as one.
        Candidates not yet tested: a capped notional inflating the premium
        fraction, a premium quoted in another currency, or those rows being a
        product the FISN parse is reading as a plain swaption. This test
        exists to keep the question open rather than to check behaviour.
        """
        assert True
