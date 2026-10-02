"""Collateral cash flows for a mortgage pool — the input a waterfall divides.

Implements the cash-flow half of specification §10.3. The *prepayment model*
proper — the fitted S-curve, burnout, seasoning and loan characteristics —
is deliberately not here. §10.3 states that "the pricing engine takes the
prepayment model as a pluggable interface", and this module is what that
interface feeds: a monthly speed, from wherever.

That separation is not tidiness. Fitting the model needs Fannie and Freddie
loan-level data, whose licences restrict what may be *distributed* (see
`config/completion.yaml`, P4_3). The waterfall needs none of it. Coupling
them would have put a licence question in front of arithmetic that has no
licence question, which is how P4_3 spent two months marked `terms` when
two thirds of it was never blocked.

## What a monthly speed means here

Three quantities name the same thing at different scales, and confusing them
is a classic source of errors that are wrong by a factor of twelve:

* **SMM** — single monthly mortality. The fraction of the balance remaining
  *after scheduled amortisation* that prepays this month.
* **CPR** — the annualised equivalent. `CPR = 1 - (1 - SMM)**12`.
* **PSA** — a multiple of a standard ramp, not a rate at all. 100 PSA is
  0.2% CPR in month one rising 0.2% a month to 6% at month 30, flat
  thereafter. 200 PSA is twice that *ramp*, which is not the same as twice
  the CPR once the ramp has topped out — it is 12%.

The ramp is a market convention with a fixed shape, so it is written as
constants rather than parameters. Anything that wants a different shape
wants its own model, which is what the pluggable interface is for.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from treble.analytics.registry import model

#: The PSA standard ramp: CPR rises by this much each month...
PSA_RAMP_STEP = 0.002

#: ...for this many months...
PSA_RAMP_MONTHS = 30

#: ...reaching this CPR, and staying there. 30 months at 0.2% is 6%, so
#: redundant arithmetic — and it is stated anyway, because the ramp is a
#: convention rather than a formula and a later edit to either constant
#: should have to confront the third rather than silently contradict it.
#: `TestThePsaRampIsSelfConsistent` asserts the three agree.
PSA_PLATEAU_CPR = 0.06

#: Months in a year. Named because `/ 12` appearing beside `** 12` and
#: `** (1 / 12)` in the same file is exactly where a factor-of-twelve error
#: hides, and a reader checking the algebra should not have to decide which
#: twelve is which.
MONTHS_PER_YEAR = 12


class MortgagePool(BaseModel):
    """The collateral: one pool, treated as a single weighted-average loan.

    A real pool is thousands of loans with a distribution of coupons and
    terms, and §10.3's prepayment model keys off exactly that distribution
    (LTV, FICO, loan size, geography, servicer). This representation cannot
    express any of it.

    It is the right input for a *waterfall* regardless, because a waterfall
    divides whatever cash arrives and does not care how the pool produced
    it. When the loan-level model lands it will produce a monthly speed per
    period, and `flows` already takes one of those per period.
    """

    model_config = ConfigDict(frozen=True)

    original_balance: float = Field(gt=0)
    #: Weighted-average coupon, annual, as a decimal. The *gross* rate the
    #: borrowers pay. The pass-through rate investors receive is lower by
    #: the servicing and guarantee fee, which belongs to the tranche
    #: coupons rather than here.
    wac: float = Field(gt=0, lt=1)
    #: Weighted-average maturity in months, at origination.
    wam_months: int = Field(gt=0)
    #: Months already elapsed. Drives the PSA ramp, and is the one field
    #: that makes two otherwise identical pools prepay differently.
    age_months: int = Field(ge=0)


class PeriodFlow(BaseModel):
    """One month of collateral cash, decomposed.

    Principal is split rather than totalled because a waterfall can treat
    the two differently — PAC schedules are met from scheduled principal
    with prepayment absorbed by the support tranche — and a single
    `principal` field would make that distinction unrecoverable downstream.
    """

    model_config = ConfigDict(frozen=True)

    month: int
    opening_balance: float
    interest: float
    scheduled_principal: float
    prepayment: float
    closing_balance: float

    @property
    def principal(self) -> float:
        return self.scheduled_principal + self.prepayment


def _smm_from_cpr(cpr: float) -> float:
    """Monthly mortality from an annual rate.

    `1 - (1 - cpr) ** (1 / 12)`, not `cpr / 12`. Measured at 6% CPR — the
    100 PSA plateau — the true SMM is **0.00514301** against the linear
    0.00500000, so `cpr / 12` **understates** monthly prepayment by 2.78%
    of the figure. Small enough to survive eyeballing, and it compounds
    over 360 periods into a WAL that is wrong in the slow direction.

    The direction is stated because it was stated backwards first: the
    test asserting it originally required `smm < cpr / 12`, which fails,
    and the arithmetic settled it rather than the other way round.
    """
    # `float ** float` is `Any` to mypy, because a negative base makes the
    # result complex. `cpr` is a rate below 1 so the base is positive, and
    # the cast says so rather than leaving an untyped hole in the one
    # function every prepayment figure passes through.
    return 1.0 - float((1.0 - cpr) ** (1.0 / MONTHS_PER_YEAR))


def _psa_cpr(psa: float, age_months: int) -> float:
    """The annual CPR a PSA multiple implies at a given age.

    The multiple scales the *ramp*, so the plateau moves with it: 200 PSA
    plateaus at 12% CPR, not at 6%. Writing this as `min(ramp, plateau)`
    with a fixed plateau would cap every speed at 6% and silently turn
    every fast scenario into the same slow one.
    """
    ramp = PSA_RAMP_STEP * min(age_months + 1, PSA_RAMP_MONTHS)
    return ramp * psa / 100.0


@model(
    model_id="mortgage.psa_smm",
    version="1",
    spec_section="§10.3",
    summary="Single monthly mortality implied by a PSA multiple at a given pool age",
)
def psa_smm(psa: float, age_months: int) -> float:
    """SMM for a PSA multiple at `age_months`. Zero PSA is zero, exactly."""
    if psa < 0:
        raise ValueError(f"psa must not be negative, got {psa}")
    return _smm_from_cpr(_psa_cpr(psa, age_months))


@model(
    model_id="mortgage.cpr_from_smm",
    version="1",
    spec_section="§10.3",
    summary="Annualised conditional prepayment rate from a single monthly mortality",
)
def cpr_from_smm(smm: float) -> float:
    """`CPR = 1 - (1 - SMM)**12` — the inverse of `_smm_from_cpr`."""
    if not 0.0 <= smm < 1.0:
        raise ValueError(f"smm must be in [0, 1), got {smm}")
    return 1.0 - (1.0 - smm) ** MONTHS_PER_YEAR


def _level_payment(balance: float, monthly_rate: float, months: int) -> float:
    """The fixed monthly payment amortising `balance` over `months`.

    The zero-rate branch is not a numerical guard — at `monthly_rate == 0`
    the annuity formula is 0/0 — and a zero-coupon pool is a legitimate
    input a test will pass, so it returns straight-line amortisation
    rather than a NaN that propagates silently into every tranche.
    """
    if months <= 0:
        return 0.0
    if monthly_rate == 0.0:
        return balance / months
    growth = (1.0 + monthly_rate) ** months
    return balance * monthly_rate * growth / (growth - 1.0)


@model(
    model_id="mortgage.collateral_flows",
    version="1",
    spec_section="§10.3",
    summary="Monthly interest, scheduled principal and prepayment for a pool at a PSA speed",
)
def collateral_flows(pool: MortgagePool, psa: float) -> tuple[PeriodFlow, ...]:
    """Project the pool's monthly cash flows at a constant PSA multiple.

    Runs to the pool's remaining term, or until the balance is gone. The
    order within a month is the one the convention requires and is not
    interchangeable: **interest on the opening balance, then scheduled
    principal, then prepayment on what survives amortisation.** Prepaying
    first would charge a month's interest on balance that had already left,
    overstating interest by roughly one month of the prepayment — small per
    period, and it compounds into the tranche that happens to be receiving
    interest at the time.

    Scheduled principal is recomputed from the *remaining* balance and term
    each month rather than taken from an origination schedule, because
    prepayment has changed the balance the annuity amortises. Using the
    original schedule is the standard error here: it over-amortises a pool
    that has prepaid and drives the balance negative.
    """
    if psa < 0:
        raise ValueError(f"psa must not be negative, got {psa}")
    monthly_rate = pool.wac / MONTHS_PER_YEAR
    remaining_term = pool.wam_months - pool.age_months
    balance = pool.original_balance
    flows: list[PeriodFlow] = []

    for step in range(max(remaining_term, 0)):
        if balance <= 0.0:
            break
        interest = balance * monthly_rate
        payment = _level_payment(balance, monthly_rate, remaining_term - step)
        # Never amortise more than is outstanding: in the final period
        # rounding can make the annuity payment exceed the balance, and an
        # unclamped subtraction leaves a balance of -0.004 that the
        # conservation tests then report as a real leak.
        scheduled = min(max(payment - interest, 0.0), balance)
        after_scheduled = balance - scheduled
        smm = psa_smm(psa, pool.age_months + step).value
        prepayment = after_scheduled * smm
        closing = after_scheduled - prepayment
        flows.append(
            PeriodFlow(
                month=pool.age_months + step + 1,
                opening_balance=balance,
                interest=interest,
                scheduled_principal=scheduled,
                prepayment=prepayment,
                closing_balance=closing,
            )
        )
        balance = closing

    return tuple(flows)


__all__ = [
    "MONTHS_PER_YEAR",
    "PSA_PLATEAU_CPR",
    "PSA_RAMP_MONTHS",
    "PSA_RAMP_STEP",
    "MortgagePool",
    "PeriodFlow",
    "collateral_flows",
    "cpr_from_smm",
    "psa_smm",
]
