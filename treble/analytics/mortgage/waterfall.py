"""The waterfall: collateral cash run through a deal's legal priority.

Implements the distribution half of specification §10.3. Takes the flows
from `collateral` and the structure from `deal` and produces, per month,
what each class actually receives.

## Nothing is netted, and nothing is silently absorbed

Every period reports interest due, interest paid, principal paid and any
**shortfall**, as separate figures. A single "received" number would make
the two ways of receiving less than you are owed — the collateral did not
produce enough, versus the class ahead took it — indistinguishable, and
those are different securities.

The same applies at the bottom: collateral interest left after every class
is paid goes to an explicit `residual`, and so does principal arriving
after every class is retired. Dropping either would make the engine's
arithmetic *look* conservative while losing cash, which is the failure
`TestNothingIsCreatedOrDestroyed` exists to refuse. A deal that over- or
under-collateralises should show it, not have it quietly discarded.

## Interest before principal, in priority order

Both orderings are load-bearing and neither is a preference:

* **Interest first.** Interest accrues on the *opening* balance. Paying
  principal first would shrink the balance before the coupon is computed
  and understate every class's interest by one period of its own
  amortisation.
* **Priority within interest, not just within principal.** A class senior
  on principal is senior on interest too. Pro-rating a shortfall across
  classes instead would hand the junior class cash the senior is owed —
  which is the economics of a pari passu structure, not a sequential one.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from treble.analytics.mortgage.collateral import MONTHS_PER_YEAR, PeriodFlow
from treble.analytics.mortgage.deal import Deal, PrincipalRule
from treble.analytics.registry import model

#: Cash below this is treated as zero when deciding whether a class is
#: retired or whether principal remains to allocate.
#:
#: Not a tolerance on the *answer* — the reported figures are never
#: rounded. It exists because a balance of 3e-9 left by binary floating
#: point would otherwise keep a retired class in the allocation forever,
#: and in `PRO_RATA` would hand it a proportional share of every
#: subsequent payment. Sized well below a currency unit and well above
#: double-precision noise on balances of 1e9.
CASH_EPSILON = 1e-6


class TrancheDistribution(BaseModel):
    """What one class received in one period, and what it was owed."""

    model_config = ConfigDict(frozen=True)

    name: str
    opening_balance: float
    interest_due: float
    interest_paid: float
    principal_paid: float
    closing_balance: float

    @property
    def interest_shortfall(self) -> float:
        """Interest owed and not paid, because the collateral fell short.

        A positive value in an agency deal means the structure is
        infeasible — the classes promise more coupon than the collateral
        produces — rather than that a payment was late. Reported rather
        than raised, because a deal parsed from a prospectus is exactly
        where an infeasible structure comes from and the figure is the
        evidence for fixing the transcription.
        """
        return self.interest_due - self.interest_paid


class WaterfallPeriod(BaseModel):
    """One month of distributions across every class, plus the residual."""

    model_config = ConfigDict(frozen=True)

    month: int
    distributions: tuple[TrancheDistribution, ...]
    #: Collateral interest not owed to any class. The servicing and
    #: guarantee strip plus any excess spread.
    residual_interest: float
    #: Principal arriving after every class is retired. Non-zero only in an
    #: under-issued deal, and the figure that proves it.
    residual_principal: float

    @property
    def interest_paid(self) -> float:
        return sum(d.interest_paid for d in self.distributions)

    @property
    def principal_paid(self) -> float:
        return sum(d.principal_paid for d in self.distributions)


def _allocate_sequential(available: float, balances: list[float]) -> list[float]:
    """Strict priority: fill each class in turn until the cash runs out."""
    paid = [0.0] * len(balances)
    remaining = available
    for index, balance in enumerate(balances):
        if remaining <= CASH_EPSILON:
            break
        take = min(remaining, balance)
        paid[index] = take
        remaining -= take
    return paid


def _allocate_pro_rata(available: float, balances: list[float]) -> list[float]:
    """Proportional to current balance, capped at it. One pass, and that is enough.

    **The proof matters, because the defensive version is wrong in an
    instructive way.** The obvious implementation iterates: allocate
    proportionally, cap any class whose share exceeds its balance, then
    redistribute what the capped classes could not take. That is what was
    written here first, with a comment explaining that a single pass would
    "leave the remainder undistributed in exactly the final periods where
    classes retire".

    No such period exists. A class is capped when
    ``available * room_i / total_room > room_i``, which reduces to
    ``available > total_room`` — a condition with **no** ``i`` in it. So
    either every class is capped or none is, there is no mixed state, and
    the redistribution loop's second pass can never execute. The comment
    described a scenario that cannot occur, and nothing tested it: failure
    mode E, in code written the same afternoon as a commit about failure
    mode C(ii).

    What the two real cases do:

    * ``available <= total_room`` — nobody is capped, and the shares sum to
      ``available`` exactly.
    * ``available > total_room`` — everybody is capped, each class takes its
      balance, and the excess is principal arriving after the deal is fully
      repaid. The caller reports it as ``residual_principal`` rather than
      forcing it onto a class that does not owe it.
    """
    total_room = sum(b for b in balances if b > CASH_EPSILON)
    if total_room <= CASH_EPSILON or available <= CASH_EPSILON:
        return [0.0] * len(balances)
    return [
        min(available * balance / total_room, balance) if balance > CASH_EPSILON else 0.0
        for balance in balances
    ]


@model(
    model_id="mortgage.waterfall",
    version="1",
    spec_section="§10.3",
    summary="Collateral cash flows distributed through a CMO's tranche priority",
)
def run_waterfall(deal: Deal, flows: tuple[PeriodFlow, ...]) -> tuple[WaterfallPeriod, ...]:
    """Distribute `flows` through `deal`, one period at a time.

    Returns one `WaterfallPeriod` per collateral period, including periods
    after every class is retired — those show the cash falling to the
    residual, which is how an under-issued deal becomes visible rather
    than the extra periods simply not appearing.
    """
    balances = [t.original_balance for t in deal.tranches]
    coupons = [t.coupon / MONTHS_PER_YEAR for t in deal.tranches]
    periods: list[WaterfallPeriod] = []

    for flow in flows:
        opening = list(balances)
        due = [b * c for b, c in zip(opening, coupons, strict=True)]
        # Interest takes priority over interest, in class order — see the
        # module docstring on why this is not pro-rated.
        interest_paid = _allocate_sequential(flow.interest, due)
        residual_interest = flow.interest - sum(interest_paid)

        if deal.principal_rule is PrincipalRule.SEQUENTIAL:
            principal_paid = _allocate_sequential(flow.principal, opening)
        else:
            principal_paid = _allocate_pro_rata(flow.principal, opening)
        residual_principal = flow.principal - sum(principal_paid)

        balances = [b - p for b, p in zip(opening, principal_paid, strict=True)]
        periods.append(
            WaterfallPeriod(
                month=flow.month,
                distributions=tuple(
                    TrancheDistribution(
                        name=tranche.name,
                        opening_balance=open_balance,
                        interest_due=owed,
                        interest_paid=got,
                        principal_paid=principal,
                        closing_balance=close_balance,
                    )
                    for tranche, open_balance, owed, got, principal, close_balance in zip(
                        deal.tranches,
                        opening,
                        due,
                        interest_paid,
                        principal_paid,
                        balances,
                        strict=True,
                    )
                ),
                residual_interest=residual_interest,
                residual_principal=residual_principal,
            )
        )

    return tuple(periods)


@model(
    model_id="mortgage.tranche_wal",
    version="1",
    spec_section="§10.3",
    summary="Weighted average life of a tranche, in years, from its principal distributions",
)
def tranche_wal(periods: tuple[WaterfallPeriod, ...], name: str) -> float:
    """Principal-weighted average time to repayment, in years.

    Weighted by principal only. Interest carries no weight because WAL
    measures when the *principal* comes back, and a definition that
    weighted total cash would make a high-coupon class look shorter than
    an identical low-coupon one with the same amortisation.

    Returns zero for a class that receives no principal at all, which is
    the honest answer to "when is it repaid" for a tranche that never is —
    and is distinguishable from a short WAL only by reading the
    distributions, so callers that care should check.
    """
    weighted = 0.0
    total = 0.0
    for period in periods:
        for distribution in period.distributions:
            if distribution.name != name:
                continue
            weighted += distribution.principal_paid * period.month
            total += distribution.principal_paid
    if total <= CASH_EPSILON:
        return 0.0
    return weighted / total / MONTHS_PER_YEAR


__all__ = [
    "CASH_EPSILON",
    "TrancheDistribution",
    "WaterfallPeriod",
    "run_waterfall",
    "tranche_wal",
]
