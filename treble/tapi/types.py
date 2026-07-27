"""Types TAPI returns to the presentation layer (spec §8.3).

`FieldResult` lives here rather than in `render/` because it is what TAPI
*produces*; the renderer consumes it. Defining it in render would invert the
dependency — render sits above tapi in the layer order (I7) — which is
exactly what the import contract caught when it was there.
"""

from __future__ import annotations

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
