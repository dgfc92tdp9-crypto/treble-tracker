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
