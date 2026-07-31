"""Types TAPI returns to the presentation layer (spec §8.3).

`FieldResult` lives here rather than in `render/` because it is what TAPI
*produces*; the renderer consumes it. Defining it in render would invert the
dependency — render sits above tapi in the layer order (I7) — which is
exactly what the import contract caught when it was there.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict

from treble.core.provenance import ProvenanceId


class FieldResult(BaseModel):
    """One resolved field value as TAPI returns it to presentation code.

    Carries what the display conventions need without exposing storage:
    the provenance id backs SPTR drill-down (I1), `stale` drives the grey
    convention (§6.3), and `model_derived` drives the dotted underline
    (§5.4).
    """

    model_config = ConfigDict(frozen=True)

    value: str | float | int | bool | None
    provenance_id: ProvenanceId | None = None
    stale: bool = False
    model_derived: bool = False
    #: The period the value covers. Carried because a figure without its
    #: period is a figure whose meaning does not travel with it: IBM files
    #: both a half-year and a quarter of net income ending on the same day,
    #: and 2,165,000,000 unlabelled reads as an annual figure five times
    #: larger than it is. Equal dates mean an instant (a balance sheet).
    effective_from: date | None = None
    effective_to: date | None = None

    @property
    def period_label(self) -> str | None:
        """How the period reads on screen, or None if it is not known."""
        if self.effective_to is None:
            return None
        if self.effective_from is None or self.effective_from == self.effective_to:
            return f"at {self.effective_to.isoformat()}"
        months = round((self.effective_to - self.effective_from).days / 30.44)
        return f"{months} months to {self.effective_to.isoformat()}"
