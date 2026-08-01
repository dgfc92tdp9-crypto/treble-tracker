"""CDS pricing under ISDA conventions (Phase 2 `CDSW`).

Not yet the published ISDA test cases — that is the gate criterion and it is
outstanding. These pin the internal relationships that must hold whatever
the reference data says, so that when the cases are wired in a failure will
point at a convention rather than at arithmetic.
"""

from __future__ import annotations

from datetime import date

import pytest

from treble.analytics.credit.cds import (
    CdsSpec,
    hazard_from_spread,
    par_spread,
    price_cds,
)
from treble.analytics.credit.cds import _survival as survival

FIVE_YEAR = CdsSpec(
    notional=10_000_000.0,
    coupon=0.01,
    trade_date=date(2026, 6, 20),
    maturity=date(2031, 6, 20),
)


class TestParSpreadIsSelfConsistent:
    def test_a_cds_struck_at_par_settles_at_zero(self) -> None:
        """The definition of par: no money changes hands at inception.
        A non-zero upfront here means the legs disagree."""
        hazard, rate = 0.0167, 0.03
        par = par_spread(FIVE_YEAR, hazard, rate).value
        struck = FIVE_YEAR.model_copy(update={"coupon": par})
        assert price_cds(struck, hazard, rate).value.upfront == pytest.approx(0.0, abs=1e-6)

    def test_a_coupon_above_par_favours_the_seller(self) -> None:
        """The buyer overpays on the premium leg, so the upfront is
        negative: the seller pays the buyer at inception."""
        hazard, rate = 0.0167, 0.03
        par = par_spread(FIVE_YEAR, hazard, rate).value
        rich = FIVE_YEAR.model_copy(update={"coupon": par * 2})
        assert price_cds(rich, hazard, rate).value.upfront < 0.0

    def test_a_coupon_below_par_favours_the_buyer(self) -> None:
        hazard, rate = 0.0167, 0.03
        par = par_spread(FIVE_YEAR, hazard, rate).value
        cheap = FIVE_YEAR.model_copy(update={"coupon": par / 2})
        assert price_cds(cheap, hazard, rate).value.upfront > 0.0


class TestCreditTriangleIsAnApproximation:
    def test_it_is_close_to_the_solved_par_spread(self) -> None:
        """Close, which is why it seeds a solver."""
        spread = 0.01
        hazard = hazard_from_spread(spread).value
        solved = par_spread(FIVE_YEAR, hazard, 0.0).value
        assert solved == pytest.approx(spread, rel=0.05)

    def test_it_is_not_exact(self) -> None:
        """And named as an approximation for this reason. If this ever
        passes as equality, either the model changed or the triangle is
        being used where a solved spread belongs."""
        spread = 0.05
        hazard = hazard_from_spread(spread).value
        solved = par_spread(FIVE_YEAR, hazard, 0.03).value
        assert solved != pytest.approx(spread, rel=1e-6)

    def test_full_recovery_leaves_nothing_to_protect(self) -> None:
        with pytest.raises(ValueError, match="nothing to protect"):
            hazard_from_spread(0.01, recovery=1.0)


class TestMonotonicity:
    def test_wider_spreads_imply_higher_hazard(self) -> None:
        assert hazard_from_spread(0.02).value > hazard_from_spread(0.01).value

    def test_higher_hazard_lowers_survival(self) -> None:
        assert survival(0.05, 5.0) < survival(0.01, 5.0)

    def test_higher_hazard_raises_protection_value(self) -> None:
        low = price_cds(FIVE_YEAR, 0.01, 0.03).value
        high = price_cds(FIVE_YEAR, 0.05, 0.03).value
        assert high.protection_pv > low.protection_pv

    def test_higher_recovery_lowers_protection_value(self) -> None:
        """Protection pays (1 - R). Recovering more leaves less to insure."""
        low_r = price_cds(FIVE_YEAR.model_copy(update={"recovery": 0.2}), 0.02, 0.03).value
        high_r = price_cds(FIVE_YEAR.model_copy(update={"recovery": 0.6}), 0.02, 0.03).value
        assert high_r.protection_pv < low_r.protection_pv

    def test_a_longer_trade_carries_more_risk(self) -> None:
        ten_year = FIVE_YEAR.model_copy(update={"maturity": date(2036, 6, 20)})
        assert (
            price_cds(ten_year, 0.02, 0.03).value.protection_pv
            > price_cds(FIVE_YEAR, 0.02, 0.03).value.protection_pv
        )


class TestConventions:
    def test_accrued_on_default_raises_the_premium_leg(self) -> None:
        """A buyer defaulting mid-period still owes the coupon accrued to
        that day. Omitting it prices the trade in the seller's favour."""
        priced = price_cds(FIVE_YEAR, 0.05, 0.03).value
        naive_annuity = sum(
            0.25
            * (2.718281828459045 ** (-0.03 * 0.25 * i))
            * (2.718281828459045 ** (-0.05 * 0.25 * i))
            for i in range(1, 21)
        )
        assert priced.risky_pv01 > naive_annuity * FIVE_YEAR.notional * 1e-4

    def test_risky_pv01_is_positive_and_scales_with_notional(self) -> None:
        small = price_cds(FIVE_YEAR, 0.02, 0.03).value
        big = price_cds(FIVE_YEAR.model_copy(update={"notional": 20_000_000.0}), 0.02, 0.03).value
        assert small.risky_pv01 > 0
        assert big.risky_pv01 == pytest.approx(small.risky_pv01 * 2)

    def test_a_zero_hazard_gives_no_protection_value(self) -> None:
        """A name that cannot default needs no insurance."""
        assert price_cds(FIVE_YEAR, 0.0, 0.03).value.protection_pv == pytest.approx(0.0)


class TestRefusals:
    def test_a_negative_hazard_is_refused(self) -> None:
        """It would imply default becomes less likely the longer you wait."""
        with pytest.raises(ValueError, match="negative hazard"):
            price_cds(FIVE_YEAR, -0.01, 0.03)

    def test_maturity_must_follow_the_trade(self) -> None:
        backwards = FIVE_YEAR.model_copy(update={"maturity": date(2020, 1, 1)})
        with pytest.raises(ValueError, match="must follow"):
            price_cds(backwards, 0.02, 0.03)

    def test_results_carry_model_identity(self) -> None:
        result = price_cds(FIVE_YEAR, 0.02, 0.03)
        assert result.model_id == "credit.price_cds"
