"""CMO deal structure — the legal priority a waterfall executes.

Implements the structural half of specification §10.3. §10.3 names six
features: tranche priorities, PAC/TAC bands, support tranches, IO/PO
strips, floaters and inverse floaters, and residuals. **This module
implements the first.** The rest are named in `PrincipalRule` and
`CouponType` as the values they will become, so that a structure this
engine cannot yet price fails at construction with a readable name rather
than being silently mispriced as something it is not.

That choice is the point. A deal model that accepts a PAC tranche and
treats it as sequential-pay produces numbers — plausible ones — for a
security whose entire economics are the schedule it is ignoring. §10.3's
own argument for publishing waterfalls as structured data is that "a
deal's waterfall can be independently verified rather than trusted", and a
model that quietly downgrades what it is given cannot be verified against
anything.

## Why priority is a list and not a field on the tranche

Tranches carry no priority number. Priority *is* the order of
`Deal.tranches`, which means it cannot disagree with itself. A `priority:
int` on each tranche admits ties, gaps, and duplicates — three states with
no correct interpretation — and every one of them would have to be
validated back into exactly the ordering this representation starts with.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Tranche balances are reconciled against collateral to this many currency
#: units. Deal documents state balances rounded to whole currency units, so
#: a structure assembled from a prospectus will not sum to the collateral
#: exactly; a tolerance of one unit per tranche is the rounding, and
#: anything larger is a transcription error worth refusing. Not a float
#: epsilon — this is a documentation-rounding tolerance and the distinction
#: matters, because an epsilon-sized tolerance would reject every real deal.
BALANCE_TOLERANCE_PER_TRANCHE = 1.0


class CouponType(StrEnum):
    """How a tranche's interest is determined.

    Only `FIXED` is implemented. The others are declared because §10.3
    requires them and because an unimplemented member that raises by name
    is more useful than an absent one that makes a floater unrepresentable:
    the first says "not yet", the second invites someone to model it as
    fixed.
    """

    FIXED = "fixed"
    FLOATER = "floater"
    INVERSE_FLOATER = "inverse_floater"
    #: Interest-only: interest on a notional balance that receives no
    #: principal. Needs a notional distinct from a principal balance, which
    #: `Tranche` does not yet carry.
    INTEREST_ONLY = "interest_only"
    #: Principal-only: principal with no coupon. Representable as a FIXED
    #: tranche at a zero coupon, and deliberately *not* aliased to one —
    #: a PO's risk is nothing like a zero-coupon sequential's, and the two
    #: should not share a code path merely because one number matches.
    PRINCIPAL_ONLY = "principal_only"


class PrincipalRule(StrEnum):
    """How principal is allocated across tranches in a period."""

    #: Strict priority: the first tranche with a balance takes everything
    #: until retired. The canonical CMO structure.
    SEQUENTIAL = "sequential"
    #: Every tranche takes principal in proportion to its current balance.
    PRO_RATA = "pro_rata"
    #: Scheduled amortisation to a band, with prepayment variance absorbed
    #: by a support tranche. Not implemented: needs the band schedule, which
    #: is derived from two PSA speeds rather than stated in the deal.
    PAC = "pac"
    #: A single-speed schedule — PAC's upper band without the lower.
    TAC = "tac"


class Tranche(BaseModel):
    """One class of a deal.

    `coupon` is the *pass-through* rate the class receives, not the
    collateral's WAC. The difference is the servicing and guarantee strip,
    and putting it here rather than on the pool is deliberate: the strip is
    a feature of the security, pools of identical collateral back classes
    at different coupons, and a WAC net of fees would make the collateral's
    interest unrecoverable.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    original_balance: float = Field(gt=0)
    coupon: float = Field(ge=0, lt=1)
    coupon_type: CouponType = CouponType.FIXED

    @model_validator(mode="after")
    def _only_fixed_is_implemented(self) -> Tranche:
        if self.coupon_type is not CouponType.FIXED:
            raise ValueError(
                f"tranche {self.name!r}: coupon type {self.coupon_type.value!r} is declared "
                "by §10.3 but not implemented. Refused rather than treated as fixed: a "
                "floater priced at a fixed coupon returns a number for a different security."
            )
        return self


class Deal(BaseModel):
    """A CMO structure: collateral balance, and classes in priority order."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    #: Priority order. `tranches[0]` is paid first.
    tranches: tuple[Tranche, ...] = Field(min_length=1)
    principal_rule: PrincipalRule = PrincipalRule.SEQUENTIAL
    #: The collateral balance the classes were sized against. Carried so
    #: the structure can be checked without the pool, which is what lets a
    #: deal parsed from a prospectus be validated before any pool is found.
    collateral_balance: float = Field(gt=0)

    @model_validator(mode="after")
    def _structure_is_coherent(self) -> Deal:
        if self.principal_rule in {PrincipalRule.PAC, PrincipalRule.TAC}:
            raise ValueError(
                f"deal {self.name!r}: principal rule {self.principal_rule.value!r} is "
                "declared by §10.3 but not implemented. A PAC band is derived from two "
                "PSA speeds, not stated in the deal, and running one as sequential-pay "
                "ignores the schedule that is the security's entire economics."
            )
        names = [t.name for t in self.tranches]
        if len(set(names)) != len(names):
            # Distributions are reported per name, so a duplicate makes one
            # tranche's cash silently unattributable to either class.
            duplicated = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(f"deal {self.name!r}: duplicate tranche names {duplicated}")
        issued = sum(t.original_balance for t in self.tranches)
        tolerance = BALANCE_TOLERANCE_PER_TRANCHE * len(self.tranches)
        if abs(issued - self.collateral_balance) > tolerance:
            raise ValueError(
                f"deal {self.name!r}: tranche balances sum to {issued:,.2f} against "
                f"collateral of {self.collateral_balance:,.2f}, a difference of "
                f"{issued - self.collateral_balance:,.2f} beyond the {tolerance:,.2f} "
                "rounding tolerance. Over-issuance distributes principal that does not "
                "exist; under-issuance leaves principal with nowhere to go."
            )
        return self

    @property
    def total_issued(self) -> float:
        return sum(t.original_balance for t in self.tranches)


__all__ = [
    "BALANCE_TOLERANCE_PER_TRANCHE",
    "CouponType",
    "Deal",
    "PrincipalRule",
    "Tranche",
]
