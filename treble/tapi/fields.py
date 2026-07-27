"""The field dictionary (spec §9.6; `FLDS`).

Every screen binds to a field *mnemonic*, never to a storage detail. This
module is the mapping between the two, and it carries the metadata §9.6
requires: data type, override list, source, and — where a value is
model-derived — a reference into the model registry (`MDL`).

**No coined mnemonics.** CLAUDE.md is explicit that §24 and §9.6 are the
contract and that names must be asked for rather than invented. So this
dictionary contains two kinds of entry and nothing else:

1. **Documented mnemonics** — those the specification actually names
   (`PX_LAST`, `CUR_MKT_CAP`, `OAS_SPREAD_MID`, `DUR_ADJ_OAS`, `BEST_EPS`,
   `IDX_MWEIGHT`).
2. **As-reported source names** — XBRL tags carried through unchanged
   (`us-gaap:Assets:USD`). These are the filer's own vocabulary, not
   invented by us, and §14.1 requires the as-reported view to exist
   alongside the standardised one. The standardised global chart of
   accounts is Phase 2 scale (recorded as an open question in PROGRESS).

A screen asking for a mnemonic that is neither is an error, not an empty
cell: a silently blank field is indistinguishable from a genuinely missing
value, and the spec's whole provenance model depends on telling them apart.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict


class FieldType(enum.Enum):
    """§9.6 data types."""

    PRICE = "price"
    YIELD = "yield"
    SPREAD = "spread"
    DURATION = "duration"
    MONEY = "money"
    RATIO = "ratio"
    COUNT = "count"
    STRING = "string"
    DATE = "date"
    BULK = "bulk"  # multi-row table (DVD_HIST, holders, index members)


class FieldDef(BaseModel):
    """One entry in the data dictionary."""

    model_config = ConfigDict(frozen=True)

    mnemonic: str
    description: str
    field_type: FieldType
    #: The stored fact field this reads, when it is a direct lookup.
    #: None for model-derived fields, which are computed by analytics.
    stored_field: str | None = None
    #: Parameters that change how the value is computed (§9.6 override list).
    overrides: tuple[str, ...] = ()
    #: Model registry id when the value is a model output rather than a
    #: stored fact — drives the dotted-underline display convention (§5.4).
    model_id: str | None = None
    #: Sources this field can come from, for provenance display.
    sources: tuple[str, ...] = ()

    @property
    def model_derived(self) -> bool:
        return self.model_id is not None


class UnknownFieldError(KeyError):
    """A screen bound to a mnemonic that is not in the dictionary."""


#: Mnemonics the specification itself names (§9.6, §24). Anything added
#: here must be traceable to the spec — see the module docstring.
_SPEC_FIELDS: tuple[FieldDef, ...] = (
    FieldDef(
        mnemonic="PX_LAST",
        description="Last price",
        field_type=FieldType.PRICE,
        stored_field="PX_LAST",
        overrides=("PX_OVERRIDE", "SETTLE_DT_OVERRIDE"),
        sources=("fred", "trace-api"),
    ),
    FieldDef(
        mnemonic="CUR_MKT_CAP",
        description="Current market capitalisation",
        field_type=FieldType.MONEY,
        sources=("edgar",),
    ),
    FieldDef(
        mnemonic="OAS_SPREAD_MID",
        description="Option-adjusted spread, mid",
        field_type=FieldType.SPREAD,
        overrides=(
            "OAS_VOL_OVERRIDE",
            "OAS_MODEL_OVERRIDE",
            "PX_OVERRIDE",
            "SETTLE_DT_OVERRIDE",
            "CURVE_OVERRIDE",
        ),
        model_id="bonds.oas_hull_white_lattice",
        sources=("model",),
    ),
    FieldDef(
        mnemonic="DUR_ADJ_OAS",
        description="Option-adjusted (effective) duration",
        field_type=FieldType.DURATION,
        overrides=("OAS_VOL_OVERRIDE", "CURVE_OVERRIDE"),
        model_id="bonds.effective_duration",
        sources=("model",),
    ),
    FieldDef(
        mnemonic="BEST_EPS",
        description="Consensus earnings per share estimate",
        field_type=FieldType.RATIO,
        sources=("contributed", "model"),
    ),
    FieldDef(
        mnemonic="IDX_MWEIGHT",
        description="Index member weight",
        field_type=FieldType.RATIO,
        sources=("edgar",),
    ),
)


def _as_reported(stored_field: str) -> FieldDef:
    """Wrap an as-filed source tag as a dictionary entry.

    The mnemonic *is* the source's own name (`us-gaap:Assets:USD`), so
    nothing is invented and `SPTR` traces straight back to the filing.
    """
    taxonomy = stored_field.split(":", 1)[0]
    return FieldDef(
        mnemonic=stored_field,
        description=f"As-reported {stored_field}",
        field_type=FieldType.MONEY if stored_field.endswith(":USD") else FieldType.STRING,
        stored_field=stored_field,
        sources=("edgar",) if taxonomy in ("us-gaap", "dei", "ifrs-full") else (taxonomy,),
    )


class FieldDictionary:
    """Lookup over documented mnemonics plus as-reported source tags."""

    def __init__(self, extra: tuple[FieldDef, ...] = ()) -> None:
        self._defs: dict[str, FieldDef] = {f.mnemonic: f for f in (*_SPEC_FIELDS, *extra)}

    def __contains__(self, mnemonic: str) -> bool:
        return mnemonic in self._defs or self._is_as_reported(mnemonic)

    @staticmethod
    def _is_as_reported(mnemonic: str) -> bool:
        # taxonomy:Tag:unit — the shape the ingest adapters emit.
        parts = mnemonic.split(":")
        return len(parts) == 3 and all(parts)

    def get(self, mnemonic: str) -> FieldDef:
        if mnemonic in self._defs:
            return self._defs[mnemonic]
        if self._is_as_reported(mnemonic):
            return _as_reported(mnemonic)
        raise UnknownFieldError(
            f"{mnemonic!r} is not in the field dictionary. Screens must bind to a "
            "documented mnemonic (spec §9.6/§24) or an as-reported source tag; "
            "a blank cell would be indistinguishable from a missing value."
        )

    def search(self, query: str, *, limit: int = 50) -> list[FieldDef]:
        """`FLDS` — substring search over mnemonic and description."""
        needle = query.strip().upper()
        hits = [
            f
            for f in self._defs.values()
            if needle in f.mnemonic.upper() or needle in f.description.upper()
        ]
        return sorted(hits, key=lambda f: f.mnemonic)[:limit]

    def documented(self) -> list[FieldDef]:
        return sorted(self._defs.values(), key=lambda f: f.mnemonic)


#: Process-wide dictionary. Plugins extend it by constructing their own.
FIELDS = FieldDictionary()
