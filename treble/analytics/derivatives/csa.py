"""Collateral agreements and the curve they imply (spec §11.1, §12.1).

    `SWPM` accepts the collateral agreement (currency, rate, thresholds)
    and values accordingly. This is not cosmetic; it moves a long-dated
    swap's PV materially.

The CSA is the answer to "what rate is the money worth". A collateralised
derivative is funded by the collateral it holds, so it discounts at the rate
that collateral earns — not at a risk-free rate, not at LIBOR, and not at
whatever curve happened to be nearest to hand. Two identical swaps under
different CSAs are different trades and carry different PVs.

The failure this module exists to prevent is not a wrong formula. It is a
**fallback**: a swap that was asked to price under a EUR-collateral CSA,
found no EUR discount curve, and quietly discounted at USD OIS instead. The
PV that comes back is a perfectly reasonable number, off by the
cross-currency basis, with nothing on screen to say so. So resolution here
either returns the named curve or raises — there is no default, no nearest
match, and no "self".

**Thresholds are refused rather than ignored.** A CSA with a non-zero
threshold leaves exposure below it uncollateralised, so the trade is not
funded at the collateral rate over its whole life and discounting entirely
at that rate overstates it. Modelling the partial case properly is XVA
(spec §13) and is not in this phase. Silently treating a $10m-threshold CSA
as fully collateralised would be a number nobody could see was wrong, so
this refuses and says what is missing.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict, Field

from treble.analytics.curves.bootstrap import Curve
from treble.analytics.curves.multicurve import CurveSet, UnknownCurveError


class Collateral(enum.Enum):
    """How the trade is funded.

    ``NONE`` is a distinct regime rather than "a CSA with no terms": an
    uncollateralised trade discounts at the funding curve, which is a
    different curve and a different number. Making it an explicit member
    means an uncollateralised trade cannot be priced by leaving fields at
    their defaults.
    """

    CASH = "cash"
    NONE = "none"


class CsaUnsupportedError(NotImplementedError):
    """The agreement's terms are outside what this model values honestly."""


class CsaTerms(BaseModel):
    """The collateral agreement, reduced to what changes the discounting."""

    model_config = ConfigDict(frozen=True)

    #: Currency the collateral is posted in. Recorded even though the curve
    #: name carries the discounting, because a mismatch between the two is a
    #: configuration error a reader can see and a program cannot infer.
    collateral_currency: str = Field(pattern=r"^[A-Z]{3}$")
    #: The curve remunerating the collateral. For a cash CSA in the trade's
    #: own currency this is that currency's overnight curve; for a foreign
    #: CSA it is the cross-currency-basis-adjusted curve
    #: (:func:`~treble.analytics.curves.multicurve.build_csa_discount_curve`);
    #: for an uncollateralised trade it is the funding curve.
    discount_curve: str
    collateral: Collateral = Collateral.CASH
    #: Exposure below this is uncollateralised. Non-zero is refused, not
    #: approximated — see the module docstring.
    threshold: float = Field(default=0.0, ge=0.0)
    #: Smallest transfer the agreement moves. Recorded but not modelled: an
    #: MTA changes *when* collateral moves, not what rate it earns, so it
    #: does not enter the discounting basis. Stated here so that its
    #: absence from the calculation is a documented decision rather than an
    #: oversight.
    minimum_transfer_amount: float = Field(default=0.0, ge=0.0)

    @property
    def label(self) -> str:
        """One line for the screen: what this trade is funded at."""
        if self.collateral is Collateral.NONE:
            return f"Uncollateralised · funding {self.discount_curve}"
        return f"{self.collateral_currency} cash CSA · {self.discount_curve}"

    def resolve(self, curves: CurveSet) -> Curve:
        """The curve this agreement discounts at, or a refusal.

        Never falls back. A failure here is a configuration that has not
        been completed; a fallback would be a valuation that silently
        changed what it was asked to value.

        A method rather than a free function, deliberately: the I3 registry
        walk requires every public module-level callable under
        ``treble.analytics`` to carry a model envelope, and adding this
        module to that check's exclusion list to accommodate one function
        would widen an invariant to fit the code. Resolving a CSA is an
        operation on the agreement, so it belongs on the agreement.
        """
        if self.threshold > 0.0:
            raise CsaUnsupportedError(
                f"CSA threshold of {self.threshold:,.0f} leaves exposure below it "
                f"uncollateralised, so the trade is not funded at {self.discount_curve} "
                "over its whole life. Valuing it as though it were would overstate the "
                "PV by an amount this model cannot quantify without XVA (spec §13)"
            )
        try:
            return curves.curve(self.discount_curve)
        except UnknownCurveError as exc:
            raise UnknownCurveError(
                f"CSA names {self.discount_curve!r} as the collateral rate and the curve "
                f"set does not contain it (has: {', '.join(curves.names)}). Refusing "
                "rather than discounting at another curve: the PV would be wrong by the "
                "basis between them and would look entirely ordinary"
            ) from exc


__all__ = [
    "Collateral",
    "CsaTerms",
    "CsaUnsupportedError",
]
