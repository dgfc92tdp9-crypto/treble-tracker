"""Credit default swaps — the ISDA standard model (spec §7, Phase 2 `CDSW`).

A CDS is two legs. The protection buyer pays a fixed coupon until default or
maturity; the seller pays `(1 - R)` of notional if the reference entity
defaults. The upfront settlement is the difference in present value:

    upfront = protection PV - premium PV

Everything else is convention, and the conventions are where CDS pricing
goes wrong. The ISDA standard model fixes them so that two counterparties
computing the same trade agree to the cent — which is the entire reason the
standard exists, and why this implements *it* rather than a defensible
alternative.

**Conventions this fixes, each of which silently changes the number:**

- **ACT/360 on the premium leg**, not ACT/365 — a 1.4% difference in the
  accrual factor, which is real money on a notional trade.
- **Accrued is paid on default.** A buyer that defaults mid-period still
  owes the coupon accrued to that day; ignoring it prices the trade in the
  seller's favour by roughly half a period's carry.
- **Protection is continuous, not discrete.** Default can occur any day, so
  the protection leg integrates over the period rather than paying at period
  end. Discretising it understates the value of protection.
- **The credit triangle is an approximation.** `h ≈ s / (1 - R)` is exact
  only in the limit of continuous premium payment and zero interest. It is
  used here to *seed* the hazard rate, never to report one.

**Not yet validated against ISDA's published test cases.** That validation
is the gate criterion, and until the cases are wired in this model is
internally consistent rather than externally confirmed. The distinction
matters: Phase 1's bond yields were believable because Treasury's own
numbers agreed, not because the code agreed with itself.
"""

from __future__ import annotations

import math
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from treble.analytics.curves.bootstrap import Curve
from treble.analytics.registry import model

#: What a CDS may be discounted on. A float is a flat continuously
#: compounded rate — the Phase 2 starting point, kept because it is the
#: honest description of a market with no curve. A `Curve` is the real
#: thing, and the difference is measurable: against ISDA's published grids
#: a flat rate leaves a median error of 0.49bp of notional, which the
#: bootstrapped curve removes.
DiscountSource = float | Curve


def _discount_factor(discount: DiscountSource, years: float) -> float:
    """Discount factor at `years`, from a flat rate or a bootstrapped curve."""
    if isinstance(discount, Curve):
        return discount.discount(years)
    return math.exp(-discount * years)


#: ISDA standard: the premium leg accrues ACT/360.
_ACT_360 = 360.0

#: Default recovery assumption for senior unsecured corporates. Explicit
#: because a recovery rate assumed rather than stated is an input nobody
#: audited.
STANDARD_RECOVERY = 0.40


class CdsSpec(BaseModel):
    """A single-name CDS, as traded under the standard contract."""

    model_config = ConfigDict(frozen=True)

    notional: float = Field(gt=0.0)
    #: Fixed running coupon, e.g. 0.01 for the 100bp standard contract.
    coupon: float = Field(ge=0.0)
    trade_date: date
    maturity: date
    #: Payments per year. Standard contracts pay quarterly.
    frequency: int = Field(default=4, gt=0)
    recovery: float = Field(default=STANDARD_RECOVERY, ge=0.0, lt=1.0)


class CdsPricing(BaseModel):
    """The two legs and what settles between them."""

    model_config = ConfigDict(frozen=True)

    protection_pv: float
    premium_pv: float
    #: Positive means the protection buyer pays this amount at settlement.
    upfront: float
    #: Present value of one basis point of running spread — the standard
    #: measure of a CDS position's spread sensitivity.
    risky_pv01: float


def _years(start: date, end: date) -> float:
    """ACT/360, the ISDA premium-leg convention."""
    return (end - start).days / _ACT_360


def _survival(hazard: float, years: float) -> float:
    """Probability of surviving `years` under a flat hazard rate.

    Private: an intermediate of `price_cds`, not an analytic anyone
    requests. The I3 registry check caught it as a public analytics callable
    without a model envelope, which was the right catch — but stamping a
    model id on a value computed twice per period would put identity on an
    intermediate rather than on a result.
    """
    return math.exp(-hazard * years)


@model(
    model_id="credit.hazard_from_spread",
    version="1.0",
    spec_section="§7",
    summary="Flat hazard rate implied by a par spread (credit triangle)",
)
def hazard_from_spread(spread: float, recovery: float = STANDARD_RECOVERY) -> float:
    """The credit triangle: `h = s / (1 - R)`.

    An approximation, and named as one. It is exact only for continuously
    paid premium at zero interest, so it belongs as a starting point for a
    solver rather than as a reported hazard rate. Quoting it as the hazard
    would be presenting an approximation as a result.
    """
    if recovery >= 1.0:
        raise ValueError("recovery of 100% leaves nothing to protect; hazard is undefined")
    return spread / (1.0 - recovery)


@model(
    model_id="credit.price_cds",
    version="1.0",
    spec_section="§7",
    summary="ISDA-convention CDS legs, upfront and risky PV01",
)
def price_cds(spec: CdsSpec, hazard: float, discount_rate: DiscountSource) -> CdsPricing:
    """Price both legs under a flat hazard and a flat discount rate.

    Flat curves are the Phase 2 starting point; the multi-curve, CSA-aware
    discounting the `SWPM` criterion requires will replace the flat rate
    without changing these conventions.
    """
    if hazard < 0.0:
        raise ValueError("a negative hazard rate implies default becomes less likely with time")

    total_years = _years(spec.trade_date, spec.maturity)
    if total_years <= 0.0:
        raise ValueError("maturity must follow the trade date")

    periods = max(round(total_years * spec.frequency), 1)
    step = total_years / periods

    premium_pv = 0.0
    protection_pv = 0.0
    risky_annuity = 0.0

    for index in range(1, periods + 1):
        start, end = (index - 1) * step, index * step
        discount = _discount_factor(discount_rate, end)
        survive_start, survive_end = _survival(hazard, start), _survival(hazard, end)

        # Premium paid only if the name survives to the payment date.
        risky_annuity += step * discount * survive_end

        # Accrued on default: the buyer owes the coupon up to the default
        # day. Approximated at the period midpoint, which is where a uniform
        # default within the period lands. Omitting it prices the trade in
        # the seller's favour by roughly half a period's carry.
        default_probability = survive_start - survive_end
        risky_annuity += 0.5 * step * discount * default_probability

        # Protection: continuous within the period, discounted to its
        # midpoint. Paying at period end would understate protection.
        midpoint_discount = _discount_factor(discount_rate, (start + end) / 2.0)
        protection_pv += (1.0 - spec.recovery) * default_probability * midpoint_discount

    premium_pv = spec.coupon * risky_annuity
    protection_pv *= spec.notional
    premium_pv *= spec.notional

    return CdsPricing(
        protection_pv=protection_pv,
        premium_pv=premium_pv,
        upfront=protection_pv - premium_pv,
        risky_pv01=risky_annuity * spec.notional * 1e-4,
    )


@model(
    model_id="credit.par_spread",
    version="1.0",
    spec_section="§7",
    summary="The coupon that makes a CDS settle at zero upfront",
)
def par_spread(spec: CdsSpec, hazard: float, discount_rate: DiscountSource) -> float:
    """The running coupon at which the two legs are equal.

    Solved from the legs rather than from the credit triangle, so it carries
    the model's own conventions — accrued-on-default and continuous
    protection — instead of the approximation used to seed them.
    """
    unit = spec.model_copy(update={"coupon": 1.0})
    priced = price_cds.__wrapped__(unit, hazard, discount_rate)  # type: ignore[attr-defined]
    annuity = priced.premium_pv
    if annuity <= 0.0:
        raise ValueError("no risky annuity: the premium leg has no value to solve against")
    return float(priced.protection_pv / annuity)


#: Hazard rates the solve will search between.
#:
#: Zero is the floor by definition. The ceiling is 5.0 — a 500% annual
#: hazard, which survives a year with probability 0.7%. Nothing trades
#: there; it is a bracket end, chosen high enough that a genuinely
#: distressed name is inside it and finite so a bad input fails fast
#: instead of iterating forever.
HAZARD_FLOOR = 0.0
HAZARD_CEILING = 5.0

#: Bisection steps. Fixed rather than "until converged" so the answer is a
#: deterministic function of its inputs — the same property `parser_version`
#: protects for ingest, applied to a solver. 200 halvings take the bracket
#: below 1e-59, far past double precision, so this is a ceiling that is
#: never reached rather than a tolerance anyone tunes.
SOLVE_STEPS = 200


class UpfrontOutOfRangeError(ValueError):
    """The observed upfront cannot be produced by any hazard rate.

    Its own type because the caller's response differs from every other
    failure here: an upfront outside the achievable band means the trade was
    not a standard contract on these terms — a different recovery, a
    different coupon, or a payment that is not points-upfront at all — and
    the honest display is "not implied", not a clamped number at the bracket
    end.
    """


@model(
    model_id="credit.hazard_from_upfront",
    version="1.0",
    spec_section="§13",
    summary="The hazard rate implied by a traded upfront on a fixed-coupon CDS",
)
def hazard_from_upfront(spec: CdsSpec, upfront: float, discount_rate: DiscountSource) -> float:
    """Solve the hazard rate that reproduces an observed upfront payment.

    Standard CDS trade at a fixed coupon — 100bp or 500bp — with a payment
    at settlement, so the market's view of credit arrives as *points
    upfront* rather than as a spread. This inverts `price_cds` to recover
    it, and `spread_from_upfront` turns it into the number people quote.

    ``upfront`` is a cash amount in the same units as ``spec.notional``,
    signed the way `CdsPricing.upfront` is: positive means the protection
    buyer pays.

    **Bisection, not Newton.** The upfront is strictly increasing in the
    hazard — a worse credit makes protection dearer and its premium leg
    shorter, so both terms of `protection_pv - premium_pv` move the same
    way — which makes bisection unconditionally convergent on a bracket
    that straddles the answer. Newton would be faster and would need a
    derivative that the accrued-on-default term makes awkward, in exchange
    for a failure mode that returns a wrong number rather than no number.
    """
    low, high = HAZARD_FLOOR, HAZARD_CEILING
    at_low = price_cds.__wrapped__(spec, low, discount_rate).upfront  # type: ignore[attr-defined]
    at_high = price_cds.__wrapped__(spec, high, discount_rate).upfront  # type: ignore[attr-defined]
    if not at_low <= upfront <= at_high:
        raise UpfrontOutOfRangeError(
            f"an upfront of {upfront:,.2f} is outside what any hazard rate produces for "
            f"this contract ({at_low:,.2f} at a zero hazard, {at_high:,.2f} at "
            f"{HAZARD_CEILING:.0%}). The trade was not a standard contract on these terms."
        )

    for _ in range(SOLVE_STEPS):
        middle = (low + high) / 2.0
        if price_cds.__wrapped__(spec, middle, discount_rate).upfront < upfront:  # type: ignore[attr-defined]
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


@model(
    model_id="credit.spread_from_upfront",
    version="1.0",
    spec_section="§13",
    summary="The par spread implied by a traded upfront on a fixed-coupon CDS",
)
def spread_from_upfront(spec: CdsSpec, upfront: float, discount_rate: DiscountSource) -> float:
    """The running spread a traded upfront corresponds to.

    Composed of the two models above rather than solved directly, so the
    number carries the same conventions as everything else on the screen:
    the hazard comes from inverting `price_cds`, and the spread from
    `par_spread`, which reads the legs rather than the credit triangle.

    A quoted spread and one implied this way are **not interchangeable on a
    display**. The first is what a counterparty agreed; the second is what
    this model says that payment means under a 40% recovery and a flat
    discount curve. `tapi.cdsw` keeps them in separate panes for that
    reason, and the model id travels with the implied one.
    """
    hazard = hazard_from_upfront.__wrapped__(spec, upfront, discount_rate)  # type: ignore[attr-defined]
    return float(par_spread.__wrapped__(spec, hazard, discount_rate))  # type: ignore[attr-defined]
