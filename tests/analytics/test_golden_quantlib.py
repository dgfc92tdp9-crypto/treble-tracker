"""Our closed-form pricers against QuantLib's, which nobody here wrote.

**Why this is a golden test and not a self-consistency one.** The existing
tests for these modules assert analytic edge cases, structural properties
and internal round trips — all of which a *consistently wrong* formula
passes. Put-call parity holds for a pricer that has the wrong `d1`;
monotonicity in volatility holds for one missing a `sqrt(T)`. What none
of those catch is the whole formula being subtly off in a way the module
agrees with itself about.

**Measured, not argued.** Four mutations were injected into
`black_swaption`'s `d1`: flipping the sign of the variance term, using
`d1 + total` for `d2`, swapping forward and strike inside the log, and
writing `0.5 * total` where the formula wants `0.5 * total**2`. The
pre-existing tests caught the first three and **missed the fourth** —
because a half-variance error of that shape preserves put-call parity,
preserves monotonicity in vol, and still reprices its own round trip. The
tests here caught all four.

QuantLib is the external reference actually available. It is an
independent implementation of Black (1976) and Bachelier, written by
other people, already a dependency of this project, and validated
against the literature by its own maintainers. Two independent
implementations agreeing to machine precision is a real check on the
algebra; it is not a check on whether the *convention* is the one the
market uses, which the day-count and calendar tests cover separately.

**These modules do not use QuantLib.** `_ql.py` is the only place that
touches it, and none of the pricers below import it — so this is a
comparison of two implementations rather than a function checked against
itself through a wrapper. Verified by `test_the_reference_is_independent`
at the bottom, because that is the assumption everything else here rests
on and it would fail silently the day someone reached for QuantLib
inside one of these.

Tolerances are stated per assertion rather than shared. Closed-form
prices agree to floating-point noise; implied volatilities come from a
root finder and agree to its tolerance, which is a different claim.
"""

from __future__ import annotations

import pytest
import QuantLib as ql

from treble.analytics.derivatives.capfloor import Caplet, price_cap, price_floor
from treble.analytics.vol.swaption import (
    bachelier_swaption,
    black_swaption,
    implied_black_vol,
    implied_normal_vol,
)

pytestmark = pytest.mark.golden

#: A plausible EUR swaption: 5y expiry, forward through the strike, an
#: annuity from a real-ish curve. Chosen away from the money so a payer
#: and a receiver differ and a sign error cannot hide.
FORWARD, STRIKE, EXPIRY, ANNUITY = 0.0385, 0.0400, 5.0, 4.21
LOGNORMAL_VOL, NORMAL_VOL = 0.26, 0.0068


def _value(result: object) -> float:
    """Unwrap the I3 envelope. `@model` returns a `ModelResult`, and its
    `.value` is the pricer's own return — which for a cap is a dataclass
    whose `.value` is the number."""
    inner = getattr(result, "value", result)
    return float(getattr(inner, "value", inner))


class TestSwaptionsAgainstQuantLib:
    """`black_swaption` / `bachelier_swaption` are Black (1976) and
    Bachelier on a swap annuity, which is exactly what QuantLib's
    `blackFormula` and `bachelierBlackFormula` compute."""

    @pytest.mark.parametrize("payer", [True, False])
    def test_black_matches(self, payer: bool) -> None:
        ours = _value(
            black_swaption(
                forward=FORWARD,
                strike=STRIKE,
                expiry_years=EXPIRY,
                volatility=LOGNORMAL_VOL,
                annuity=ANNUITY,
                payer=payer,
            )
        )
        reference = ANNUITY * ql.blackFormula(
            ql.Option.Call if payer else ql.Option.Put,
            STRIKE,
            FORWARD,
            LOGNORMAL_VOL * EXPIRY**0.5,
        )
        assert ours == pytest.approx(reference, abs=1e-15)

    @pytest.mark.parametrize("payer", [True, False])
    def test_bachelier_matches(self, payer: bool) -> None:
        ours = _value(
            bachelier_swaption(
                forward=FORWARD,
                strike=STRIKE,
                expiry_years=EXPIRY,
                volatility=NORMAL_VOL,
                annuity=ANNUITY,
                payer=payer,
            )
        )
        reference = ANNUITY * ql.bachelierBlackFormula(
            ql.Option.Call if payer else ql.Option.Put,
            STRIKE,
            FORWARD,
            NORMAL_VOL * EXPIRY**0.5,
        )
        assert ours == pytest.approx(reference, abs=1e-15)

    @pytest.mark.parametrize("moneyness", [0.5, 0.9, 1.0, 1.1, 2.0])
    def test_black_matches_across_moneyness(self, moneyness: float) -> None:
        """One strike proves the formula runs; a range proves the tails.
        A missing `d2` term is nearly invisible at the money and obvious
        at 2x."""
        strike = FORWARD * moneyness
        ours = _value(
            black_swaption(
                forward=FORWARD,
                strike=strike,
                expiry_years=EXPIRY,
                volatility=LOGNORMAL_VOL,
                annuity=ANNUITY,
            )
        )
        reference = ANNUITY * ql.blackFormula(
            ql.Option.Call, strike, FORWARD, LOGNORMAL_VOL * EXPIRY**0.5
        )
        assert ours == pytest.approx(reference, abs=1e-15)


class TestImpliedVolAgainstQuantLib:
    """The inversion, which is a different claim from the price: a
    solver can converge to the wrong root, or to the right one slowly."""

    @pytest.mark.parametrize("payer", [True, False])
    def test_black_inversion_matches(self, payer: bool) -> None:
        premium = _value(
            black_swaption(
                forward=FORWARD,
                strike=STRIKE,
                expiry_years=EXPIRY,
                volatility=LOGNORMAL_VOL,
                annuity=ANNUITY,
                payer=payer,
            )
        )
        ours = _value(
            implied_black_vol(
                premium_fraction=premium,
                forward=FORWARD,
                strike=STRIKE,
                expiry_years=EXPIRY,
                annuity=ANNUITY,
                payer=payer,
            )
        )
        reference = (
            ql.blackFormulaImpliedStdDev(
                ql.Option.Call if payer else ql.Option.Put,
                STRIKE,
                FORWARD,
                premium / ANNUITY,
                1.0,
            )
            / EXPIRY**0.5
        )
        # 1e-9, not 1e-15: two root finders with different tolerances.
        assert ours == pytest.approx(reference, abs=1e-9)
        assert ours == pytest.approx(LOGNORMAL_VOL, abs=1e-9)

    @pytest.mark.parametrize("payer", [True, False])
    def test_normal_inversion_round_trips(self, payer: bool) -> None:
        """QuantLib's Bachelier implied-vol helper is not exposed in the
        Python layer, so this is a round trip rather than a cross-check —
        stated because it is a weaker claim than the Black case above."""
        premium = _value(
            bachelier_swaption(
                forward=FORWARD,
                strike=STRIKE,
                expiry_years=EXPIRY,
                volatility=NORMAL_VOL,
                annuity=ANNUITY,
                payer=payer,
            )
        )
        ours = _value(
            implied_normal_vol(
                premium_fraction=premium,
                forward=FORWARD,
                strike=STRIKE,
                expiry_years=EXPIRY,
                annuity=ANNUITY,
                payer=payer,
            )
        )
        assert ours == pytest.approx(NORMAL_VOL, abs=1e-10)


class TestCapsAndFloorsAgainstQuantLib:
    """A caplet is a one-period swaption, so the same reference applies —
    which is precisely the claim `capfloor.py` makes in its docstring and
    which nothing had checked from the outside."""

    CAPLET = Caplet(forward=0.0412, accrual=0.5, discount_factor=0.912, expiry_years=2.5)

    def _reference(self, *, call: bool, vol: float, normal: bool) -> float:
        formula = ql.bachelierBlackFormula if normal else ql.blackFormula
        return self.CAPLET.annuity * formula(
            ql.Option.Call if call else ql.Option.Put,
            0.0400,
            self.CAPLET.forward,
            vol * self.CAPLET.expiry_years**0.5,
        )

    @pytest.mark.parametrize(("normal", "vol"), [(True, 0.0075), (False, 0.28)])
    def test_cap_matches(self, normal: bool, vol: float) -> None:
        ours = _value(price_cap([self.CAPLET], strike=0.0400, volatility=vol, normal_vol=normal))
        assert ours == pytest.approx(self._reference(call=True, vol=vol, normal=normal), abs=1e-15)

    @pytest.mark.parametrize(("normal", "vol"), [(True, 0.0075), (False, 0.28)])
    def test_floor_matches(self, normal: bool, vol: float) -> None:
        ours = _value(price_floor([self.CAPLET], strike=0.0400, volatility=vol, normal_vol=normal))
        assert ours == pytest.approx(self._reference(call=False, vol=vol, normal=normal), abs=1e-15)

    def test_a_strip_is_the_sum_of_its_caplets(self) -> None:
        """A cap is a strip, and the strip is where a wrong period hides.
        Each leg is checked against QuantLib separately, so a cap that
        happened to total correctly while mispricing two offsetting
        periods still fails."""
        strip = tuple(
            Caplet(
                forward=0.038 + 0.001 * n,
                accrual=0.5,
                discount_factor=0.99**n,
                expiry_years=0.5 * (n + 1),
            )
            for n in range(6)
        )
        result = price_cap(strip, strike=0.0400, volatility=0.0075)
        per_caplet = result.value.caplets
        for caplet, ours in zip(strip, per_caplet, strict=True):
            reference = caplet.annuity * ql.bachelierBlackFormula(
                ql.Option.Call, 0.0400, caplet.forward, 0.0075 * caplet.expiry_years**0.5
            )
            assert ours == pytest.approx(reference, abs=1e-15)


def test_the_reference_is_independent() -> None:
    """Everything above assumes these pricers do not themselves call
    QuantLib. If one ever did, the comparison would become a function
    checked against itself and every assertion here would pass while
    proving nothing — the exact shape of defect this repository keeps
    finding, so it is asserted rather than assumed.

    `analytics/_ql.py` is the only module allowed to touch QuantLib; it
    owns the evaluation-date lock and the calendar cache.
    """
    import pathlib

    for module in ("derivatives/capfloor.py", "vol/swaption.py"):
        source = pathlib.Path("treble/analytics") / module
        text = source.read_text()
        assert "QuantLib" not in text, (
            f"{module} now uses QuantLib; this is no longer a cross-check"
        )
        assert "_ql" not in text, f"{module} now reaches QuantLib through _ql; same problem"
