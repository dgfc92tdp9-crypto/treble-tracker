"""The CMO waterfall: conservation, priority, and the errors it is built to refuse.

**These are not golden tests, and the distinction matters here more than
usual.** `tests/analytics/test_golden_quantlib.py` makes the argument: it
injected four mutations into a Black formula and the pre-existing
self-consistency tests caught three, missing the one that preserved
put-call parity. Conservation and monotonicity are satisfied by a
consistently wrong engine.

For a waterfall there is no independent implementation to compare against
— QuantLib has no CMO engine and nothing else in the dependency set does
either. The external reference is **published agency data**: Ginnie Mae's
REMIC 1 and REMIC 2 factor files give realised tranche factors, and
repricing a real deal against them is the check these tests cannot be.
That work is `terms`-cleared (Ginnie Mae's Terms of Data Use restricts
only privacy, and covers single-family loan-level data rather than REMIC
disclosure) and not yet done.

So what follows is deliberately split: conservation identities, *closed
form* cases where the answer is derivable by a different route than the
engine takes, and explicit refusals. The closed-form cases are the ones
doing real work, because arithmetic computed two ways is the nearest thing
to an independent implementation available until the factor files land.
"""

from __future__ import annotations

import pytest

from treble.analytics.mortgage.collateral import (
    MONTHS_PER_YEAR,
    PSA_PLATEAU_CPR,
    PSA_RAMP_MONTHS,
    PSA_RAMP_STEP,
    MortgagePool,
    collateral_flows,
    cpr_from_smm,
    psa_smm,
)
from treble.analytics.mortgage.deal import (
    CouponType,
    Deal,
    PrincipalRule,
    Tranche,
)
from treble.analytics.mortgage.waterfall import (
    run_waterfall,
    tranche_wal,
)

BALANCE = 400_000_000.0
WAC = 0.075


def _pool(age: int = 0, wam: int = 360, wac: float = WAC) -> MortgagePool:
    return MortgagePool(original_balance=BALANCE, wac=wac, wam_months=wam, age_months=age)


def _sequential_deal(rule: PrincipalRule = PrincipalRule.SEQUENTIAL) -> Deal:
    return Deal(
        name="TEST-1",
        collateral_balance=BALANCE,
        principal_rule=rule,
        tranches=(
            Tranche(name="A", original_balance=194_500_000.0, coupon=0.060),
            Tranche(name="B", original_balance=36_000_000.0, coupon=0.065),
            Tranche(name="C", original_balance=96_500_000.0, coupon=0.070),
            Tranche(name="D", original_balance=73_000_000.0, coupon=0.075),
        ),
    )


class TestThePsaConventionIsWhatItClaims:
    def test_the_ramp_constants_agree_with_each_other(self) -> None:
        # The three constants encode one convention three ways. Pinned so
        # that editing one without the others fails rather than quietly
        # describing a ramp nobody uses.
        assert pytest.approx(PSA_PLATEAU_CPR) == PSA_RAMP_STEP * PSA_RAMP_MONTHS

    def test_one_hundred_psa_plateaus_at_six_percent(self) -> None:
        plateaued = psa_smm(100.0, PSA_RAMP_MONTHS + 60).value
        assert cpr_from_smm(plateaued).value == pytest.approx(PSA_PLATEAU_CPR)

    def test_two_hundred_psa_plateaus_at_twelve_not_six(self) -> None:
        """The multiple scales the ramp, so the plateau moves with it.

        `min(ramp, PSA_PLATEAU_CPR)` with a *fixed* plateau is the natural
        misreading of the convention, and it caps every fast scenario at
        6% — turning 200, 500 and 1000 PSA into the same slow pool. This
        is the test that refuses it.
        """
        fast = cpr_from_smm(psa_smm(200.0, PSA_RAMP_MONTHS + 60).value).value
        assert fast == pytest.approx(2 * PSA_PLATEAU_CPR)
        assert fast > PSA_PLATEAU_CPR

    def test_smm_is_not_cpr_over_twelve(self) -> None:
        """The linear approximation understates monthly prepayment.

        The direction is asserted, not just the formula, so a reversion to
        `cpr / 12` fails on magnitude rather than on a digit. It is also
        asserted the way the arithmetic actually goes: this test first
        required `smm < cpr / 12`, which is backwards — at 6% CPR the true
        SMM is 0.00514301 against a linear 0.00500000, so the linear
        version is **smaller**. Computing it settled the question.
        """
        smm = psa_smm(100.0, PSA_RAMP_MONTHS + 1).value
        assert smm == pytest.approx(1.0 - (1.0 - PSA_PLATEAU_CPR) ** (1 / MONTHS_PER_YEAR))
        assert smm > PSA_PLATEAU_CPR / MONTHS_PER_YEAR
        assert (smm - PSA_PLATEAU_CPR / MONTHS_PER_YEAR) / smm == pytest.approx(0.0278, abs=5e-4)

    def test_cpr_and_smm_round_trip(self) -> None:
        for psa in (0.0, 50.0, 165.0, 400.0):
            smm = psa_smm(psa, 12).value
            assert cpr_from_smm(smm).value == pytest.approx(1.0 - (1.0 - smm) ** MONTHS_PER_YEAR)

    def test_zero_psa_is_exactly_zero(self) -> None:
        assert psa_smm(0.0, 0).value == 0.0
        assert psa_smm(0.0, 240).value == 0.0

    def test_a_negative_speed_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            psa_smm(-1.0, 0)


class TestCollateralAgainstClosedForm:
    """Derived a second way, not merely self-consistent."""

    def test_first_month_interest_is_balance_times_rate(self) -> None:
        flow = collateral_flows(_pool(), 165.0).value[0]
        assert flow.interest == pytest.approx(BALANCE * WAC / MONTHS_PER_YEAR)

    def test_interest_accrues_before_principal_is_paid(self) -> None:
        """Interest on the *opening* balance.

        Paying principal first and then accruing would understate month
        one's interest by `rate * principal`, which at these inputs is
        about £2,500 of £2,500,000 — a tenth of a percent, invisible by
        eye and wrong in every period.
        """
        flow = collateral_flows(_pool(), 165.0).value[0]
        wrong = (BALANCE - flow.principal) * WAC / MONTHS_PER_YEAR
        assert flow.interest != pytest.approx(wrong)
        assert flow.interest > wrong

    def test_with_no_prepayment_the_pool_is_a_plain_annuity(self) -> None:
        """At zero PSA the answer is the textbook annuity, computed here.

        This is the closest thing to an independent implementation in the
        file: the expected payment comes from the annuity formula applied
        once to the original balance and term, while the engine
        recomputes it every month from the *remaining* balance. The two
        agree only if the monthly recomputation is correct.
        """
        flows = collateral_flows(_pool(), 0.0).value
        rate = WAC / MONTHS_PER_YEAR
        growth = (1.0 + rate) ** 360
        expected_payment = BALANCE * rate * growth / (growth - 1.0)
        for flow in flows[:-1]:
            assert flow.interest + flow.scheduled_principal == pytest.approx(
                expected_payment, rel=1e-9
            )
            assert flow.prepayment == 0.0
        assert sum(f.principal for f in flows) == pytest.approx(BALANCE, rel=1e-9)

    def test_scheduled_principal_follows_the_remaining_balance(self) -> None:
        """Not the origination schedule.

        **Mutation-driven.** Replacing the per-period annuity with the
        constant origination payment *survived* the first version of this
        test, which only asserted `closing_balance >= 0` and a terminal
        zero. Both hold under the mutant: the `min(..., balance)` clamp
        stops it going negative and the loop breaks when the balance is
        gone, so the only symptom is a pool that amortises **too fast** —
        a WAL error, silent.

        So this recomputes the payment from each period's *own* opening
        balance and remaining term, by the annuity formula, which is a
        different route than the engine's loop.
        """
        flows = collateral_flows(_pool(), 500.0).value
        rate = WAC / MONTHS_PER_YEAR
        for index, flow in enumerate(flows[:-1]):
            term_left = 360 - index
            growth = (1.0 + rate) ** term_left
            payment = flow.opening_balance * rate * growth / (growth - 1.0)
            assert flow.scheduled_principal == pytest.approx(payment - flow.interest, rel=1e-9), (
                f"month {flow.month}"
            )
        assert all(f.closing_balance >= 0.0 for f in flows)
        assert flows[-1].closing_balance == pytest.approx(0.0, abs=1e-6)

    def test_prepayment_does_not_shorten_the_scheduled_term(self) -> None:
        # The other half of the same mutant: over-amortising retires the
        # pool early, so the period count is itself discriminating.
        assert len(collateral_flows(_pool(), 165.0).value) == 360
        assert len(collateral_flows(_pool(), 0.0).value) == 360

    def test_principal_totals_the_original_balance_at_every_speed(self) -> None:
        for psa in (0.0, 100.0, 165.0, 400.0, 1500.0):
            flows = collateral_flows(_pool(), psa).value
            total = sum(f.principal for f in flows)
            assert total == pytest.approx(BALANCE, rel=1e-9), f"leaked at {psa} PSA"

    def test_a_seasoned_pool_runs_only_its_remaining_term(self) -> None:
        assert len(collateral_flows(_pool(age=60), 0.0).value) == 300

    def test_a_zero_coupon_pool_amortises_straight_line(self) -> None:
        # The 0/0 branch in `_level_payment`. A legitimate input, and a NaN
        # here would propagate into every tranche silently.
        flows = collateral_flows(_pool(wac=1e-12), 0.0).value
        assert all(f.scheduled_principal > 0 for f in flows)
        assert sum(f.principal for f in flows) == pytest.approx(BALANCE, rel=1e-6)


class TestNothingIsCreatedOrDestroyed:
    """The conservation identities, per period and in total."""

    @pytest.mark.parametrize("rule", [PrincipalRule.SEQUENTIAL, PrincipalRule.PRO_RATA])
    @pytest.mark.parametrize("psa", [0.0, 165.0, 800.0])
    def test_every_period_distributes_exactly_what_arrived(
        self, rule: PrincipalRule, psa: float
    ) -> None:
        flows = collateral_flows(_pool(), psa).value
        periods = run_waterfall(_sequential_deal(rule), flows).value
        assert len(periods) == len(flows)
        for flow, period in zip(flows, periods, strict=True):
            assert period.interest_paid + period.residual_interest == pytest.approx(
                flow.interest, rel=1e-9, abs=1e-6
            )
            assert period.principal_paid + period.residual_principal == pytest.approx(
                flow.principal, rel=1e-9, abs=1e-6
            )

    @pytest.mark.parametrize("rule", [PrincipalRule.SEQUENTIAL, PrincipalRule.PRO_RATA])
    def test_each_class_is_repaid_exactly_its_balance(self, rule: PrincipalRule) -> None:
        deal = _sequential_deal(rule)
        periods = run_waterfall(deal, collateral_flows(_pool(), 165.0).value).value
        for tranche in deal.tranches:
            paid = sum(
                d.principal_paid for p in periods for d in p.distributions if d.name == tranche.name
            )
            assert paid == pytest.approx(tranche.original_balance, rel=1e-9), tranche.name

    @pytest.mark.parametrize("rule", [PrincipalRule.SEQUENTIAL, PrincipalRule.PRO_RATA])
    def test_no_balance_ever_goes_negative(self, rule: PrincipalRule) -> None:
        periods = run_waterfall(
            _sequential_deal(rule), collateral_flows(_pool(), 1500.0).value
        ).value
        for period in periods:
            for distribution in period.distributions:
                assert distribution.closing_balance >= -1e-6, distribution.name
                assert distribution.principal_paid <= distribution.opening_balance + 1e-6

    def test_a_feasible_deal_has_no_interest_shortfall(self) -> None:
        # Every coupon is below the WAC, so the collateral covers them all.
        periods = run_waterfall(_sequential_deal(), collateral_flows(_pool(), 165.0).value).value
        for period in periods:
            for distribution in period.distributions:
                assert distribution.interest_shortfall == pytest.approx(0.0, abs=1e-6)

    def test_the_servicing_strip_shows_up_as_residual_interest(self) -> None:
        """Coupons below the WAC leave a strip, and it is reported.

        A netted engine would absorb this into "interest paid" and the
        deal would appear to pass through its full gross coupon.
        """
        deal = _sequential_deal()
        period = run_waterfall(deal, collateral_flows(_pool(), 165.0).value).value[0]
        expected = BALANCE * WAC / MONTHS_PER_YEAR - sum(
            t.original_balance * t.coupon / MONTHS_PER_YEAR for t in deal.tranches
        )
        assert period.residual_interest == pytest.approx(expected, rel=1e-9)
        assert period.residual_interest > 0


class TestPriorityIsRespected:
    def test_a_junior_class_gets_nothing_until_its_senior_retires(self) -> None:
        deal = _sequential_deal()
        periods = run_waterfall(deal, collateral_flows(_pool(), 165.0).value).value
        senior_live = [p.month for p in periods if p.distributions[0].closing_balance > 1e-6]
        for period in periods:
            if period.month in senior_live:
                for distribution in period.distributions[1:]:
                    assert distribution.principal_paid == pytest.approx(0.0, abs=1e-6)

    def test_sequential_wals_increase_down_the_structure(self) -> None:
        deal = _sequential_deal()
        periods = run_waterfall(deal, collateral_flows(_pool(), 165.0).value).value
        wals = [tranche_wal(periods, t.name).value for t in deal.tranches]
        assert wals == sorted(wals), wals
        assert len(set(wals)) == len(wals)

    def test_pro_rata_pays_in_proportion_to_balance(self) -> None:
        deal = _sequential_deal(PrincipalRule.PRO_RATA)
        flows = collateral_flows(_pool(), 165.0).value
        period = run_waterfall(deal, flows).value[0]
        for distribution in period.distributions:
            expected = flows[0].principal * distribution.opening_balance / BALANCE
            assert distribution.principal_paid == pytest.approx(expected, rel=1e-9)

    def test_pro_rata_wals_are_identical_across_classes(self) -> None:
        # The structural difference from sequential, stated as the property
        # that distinguishes them: pro rata classes amortise together.
        deal = _sequential_deal(PrincipalRule.PRO_RATA)
        periods = run_waterfall(deal, collateral_flows(_pool(), 165.0).value).value
        wals = [tranche_wal(periods, t.name).value for t in deal.tranches]
        assert wals == pytest.approx([wals[0]] * len(wals), rel=1e-6)

    def test_pro_rata_never_caps_a_class_while_principal_fits(self) -> None:
        """The reason one allocation pass is enough.

        A tiny class beside a huge one is where capping would show up if
        it ever did. It does not: the share is proportional, so TINY takes
        0.001% of each payment and amortises at exactly BIG's rate. It is
        never retired early and never capped, which is the property that
        makes a redistribution loop unnecessary rather than merely
        unused — see `_allocate_pro_rata`.
        """
        deal = Deal(
            name="CAP-1",
            collateral_balance=100_000_000.0,
            principal_rule=PrincipalRule.PRO_RATA,
            tranches=(
                Tranche(name="TINY", original_balance=1_000.0, coupon=0.05),
                Tranche(name="BIG", original_balance=99_999_000.0, coupon=0.05),
            ),
        )
        pool = MortgagePool(original_balance=100_000_000.0, wac=0.05, wam_months=12, age_months=0)
        flows = collateral_flows(pool, 0.0).value
        periods = run_waterfall(deal, flows).value
        for flow, period in zip(flows, periods, strict=True):
            assert period.principal_paid == pytest.approx(flow.principal, rel=1e-9)
            assert period.residual_principal == pytest.approx(0.0, abs=1e-6)
            for distribution in period.distributions:
                # Never capped: the share is strictly inside the balance.
                assert distribution.principal_paid <= distribution.opening_balance + 1e-9
        tiny_fraction = 1_000.0 / 100_000_000.0
        assert periods[0].distributions[0].principal_paid == pytest.approx(
            flows[0].principal * tiny_fraction, rel=1e-9
        )

    def test_pro_rata_caps_every_class_together_or_not_at_all(self) -> None:
        """The only state in which the cap binds, and it binds for all.

        `available > total_room` has no class index in it. Built here by
        pricing a one-class pro-rata deal against collateral twice its
        size: the class is capped at its balance and the excess becomes
        residual principal rather than being forced onto a class that does
        not owe it.
        """
        deal = Deal(
            name="CAP-2",
            collateral_balance=BALANCE,
            principal_rule=PrincipalRule.PRO_RATA,
            tranches=(
                Tranche(name="X", original_balance=200_000_000.0, coupon=0.06),
                Tranche(name="Y", original_balance=200_000_000.0, coupon=0.06),
            ),
        )
        big = MortgagePool(original_balance=2 * BALANCE, wac=WAC, wam_months=360, age_months=0)
        periods = run_waterfall(deal, collateral_flows(big, 165.0).value).value
        assert sum(p.principal_paid for p in periods) == pytest.approx(BALANCE, rel=1e-9)
        assert sum(p.residual_principal for p in periods) == pytest.approx(BALANCE, rel=1e-6)
        capped = [p for p in periods if p.residual_principal > 1e-6]
        # Whenever the cap binds, no class is left with a balance: all or none.
        for period in capped:
            for distribution in period.distributions:
                assert distribution.closing_balance == pytest.approx(0.0, abs=1e-6)

    def test_a_retired_class_stops_accruing_interest(self) -> None:
        deal = _sequential_deal()
        periods = run_waterfall(deal, collateral_flows(_pool(), 165.0).value).value
        for period in periods:
            for distribution in period.distributions:
                if distribution.opening_balance <= 1e-6:
                    assert distribution.interest_due == pytest.approx(0.0, abs=1e-9)


class TestAnInterestShortfallRespectsSeniority:
    """The branch nothing reached, found by mutation.

    Swapping interest allocation from priority to pro-rata **survived**
    every test here at first, because every coupon in the standard test
    deal is below the WAC — so collateral interest always covers interest
    due, and with no shortfall the two allocations are identical. The only
    shortfall test asserted there *wasn't* one.

    An infeasible structure is not hypothetical: it is what a deal
    transcribed from a prospectus with a wrong coupon looks like, which is
    why `TrancheDistribution.interest_shortfall` reports rather than
    raises. The deal below promises 8% against 3% collateral.
    """

    @staticmethod
    def _starved() -> tuple[Deal, MortgagePool]:
        deal = Deal(
            name="SHORT-1",
            collateral_balance=BALANCE,
            tranches=(
                Tranche(name="SENIOR", original_balance=40_000_000.0, coupon=0.08),
                Tranche(name="JUNIOR", original_balance=360_000_000.0, coupon=0.08),
            ),
        )
        return deal, _pool(wac=0.03)

    def test_the_senior_class_is_paid_in_full_and_the_junior_takes_the_hit(self) -> None:
        deal, pool = self._starved()
        period = run_waterfall(deal, collateral_flows(pool, 165.0).value).value[0]
        senior, junior = period.distributions
        assert senior.name == "SENIOR"
        assert senior.interest_shortfall == pytest.approx(0.0, abs=1e-6)
        assert senior.interest_paid == pytest.approx(senior.interest_due, rel=1e-9)
        # Pro-rating would split the shortfall across both. Seniority does not.
        assert junior.interest_shortfall > 0.0
        assert junior.interest_paid < junior.interest_due

    def test_the_shortfall_is_exactly_the_missing_collateral_interest(self) -> None:
        deal, pool = self._starved()
        flows = collateral_flows(pool, 165.0).value
        periods = run_waterfall(deal, flows).value
        for flow, period in zip(flows, periods, strict=True):
            due = sum(d.interest_due for d in period.distributions)
            shortfall = sum(d.interest_shortfall for d in period.distributions)
            assert shortfall == pytest.approx(max(due - flow.interest, 0.0), rel=1e-9)
            # Starved deal: nothing is left over for the residual.
            assert period.residual_interest == pytest.approx(0.0, abs=1e-6)

    def test_a_starved_deal_still_conserves_cash(self) -> None:
        deal, pool = self._starved()
        flows = collateral_flows(pool, 165.0).value
        periods = run_waterfall(deal, flows).value
        for flow, period in zip(flows, periods, strict=True):
            assert period.interest_paid + period.residual_interest == pytest.approx(
                flow.interest, rel=1e-9, abs=1e-6
            )


class TestUnderIssuanceIsVisible:
    def test_principal_after_every_class_retires_falls_to_the_residual(self) -> None:
        """An under-issued deal reports the orphaned principal.

        Built by giving the pool a longer term than the classes can absorb:
        the classes retire, the collateral keeps paying, and the cash has
        nowhere to go. Discarding it would make the engine's conservation
        look perfect while losing money.
        """
        deal = Deal(
            name="UNDER-1",
            collateral_balance=BALANCE,
            tranches=(Tranche(name="ONLY", original_balance=BALANCE, coupon=0.06),),
        )
        # 1500 PSA retires ONLY long before the collateral stops paying?
        # No — ONLY is the whole deal, so instead shorten it by pricing
        # against a pool twice the size.
        big = MortgagePool(original_balance=2 * BALANCE, wac=WAC, wam_months=360, age_months=0)
        flows = collateral_flows(big, 165.0).value
        periods = run_waterfall(deal, flows).value
        orphaned = sum(p.residual_principal for p in periods)
        assert orphaned == pytest.approx(BALANCE, rel=1e-6)
        assert sum(p.principal_paid for p in periods) == pytest.approx(BALANCE, rel=1e-9)


class TestWhatIsRefusedRatherThanMispriced:
    """§10.3 names six features; one is implemented. The rest must refuse."""

    @pytest.mark.parametrize(
        "kind",
        [
            CouponType.FLOATER,
            CouponType.INVERSE_FLOATER,
            CouponType.INTEREST_ONLY,
            CouponType.PRINCIPAL_ONLY,
        ],
    )
    def test_an_unimplemented_coupon_type_is_refused_by_name(self, kind: CouponType) -> None:
        with pytest.raises(ValueError, match=kind.value):
            Tranche(name="X", original_balance=1.0, coupon=0.05, coupon_type=kind)

    @pytest.mark.parametrize("rule", [PrincipalRule.PAC, PrincipalRule.TAC])
    def test_an_unimplemented_principal_rule_is_refused_by_name(self, rule: PrincipalRule) -> None:
        with pytest.raises(ValueError, match=rule.value):
            Deal(
                name="X",
                collateral_balance=1.0,
                principal_rule=rule,
                tranches=(Tranche(name="A", original_balance=1.0, coupon=0.05),),
            )

    def test_duplicate_tranche_names_are_refused(self) -> None:
        with pytest.raises(ValueError, match="duplicate tranche names"):
            Deal(
                name="X",
                collateral_balance=2.0,
                tranches=(
                    Tranche(name="A", original_balance=1.0, coupon=0.05),
                    Tranche(name="A", original_balance=1.0, coupon=0.05),
                ),
            )

    def test_over_issuance_is_refused(self) -> None:
        with pytest.raises(ValueError, match="beyond the"):
            Deal(
                name="X",
                collateral_balance=100.0,
                tranches=(Tranche(name="A", original_balance=200.0, coupon=0.05),),
            )

    def test_under_issuance_is_refused_at_construction(self) -> None:
        with pytest.raises(ValueError, match="beyond the"):
            Deal(
                name="X",
                collateral_balance=200.0,
                tranches=(Tranche(name="A", original_balance=100.0, coupon=0.05),),
            )

    def test_documentation_rounding_is_tolerated(self) -> None:
        # Balances from a prospectus are stated to whole units, so a few
        # units of disagreement across four classes is rounding and not an
        # error. An epsilon-sized tolerance would reject every real deal.
        deal = Deal(
            name="ROUND-1",
            collateral_balance=1_000_000.0,
            tranches=(
                Tranche(name="A", original_balance=400_000.4, coupon=0.05),
                Tranche(name="B", original_balance=599_999.2, coupon=0.05),
            ),
        )
        assert deal.total_issued != deal.collateral_balance


class TestTheI3EnvelopeIsCarried:
    def test_every_public_analytic_returns_an_envelope(self) -> None:
        flows = collateral_flows(_pool(), 165.0)
        assert flows.model_id == "mortgage.collateral_flows"
        assert flows.parameters["psa"] == "165.0"
        periods = run_waterfall(_sequential_deal(), flows.value)
        assert periods.model_id == "mortgage.waterfall"
        assert tranche_wal(periods.value, "A").model_id == "mortgage.tranche_wal"
