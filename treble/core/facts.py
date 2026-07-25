"""Bitemporal facts — invariant I2 (CLAUDE.md §1), carrying provenance (I1).

A Fact is immutable and append-only by construction:

- ``effective_from`` / ``effective_to`` — the period the fact describes
- ``knowledge_from`` — when the system could first have known it (for
  fundamentals: the EDGAR ``accepted`` timestamp, never the period end)
- ``knowledge_to`` is **not stored** (ADR-0001): it is derived at query time
  as the superseding row's ``knowledge_from``. Restatements insert new rows;
  nothing ever updates.

``provenance_id`` is required and non-optional: provenance is part of the
value's type, not metadata bolted on (I1). A missing value is represented as
``value=None`` with provenance saying why — never by fabricating a number.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from treble.core.identifiers import TUID
from treble.core.provenance import ProvenanceId

type FactValue = float | int | str | bool | date | None


class Fact(BaseModel):
    """One bitemporal, provenance-carrying assertion about a subject."""

    model_config = ConfigDict(frozen=True)

    subject: TUID
    field: str  # field dictionary mnemonic (spec §9.6), e.g. "PX_LAST"
    value: FactValue
    effective_from: date
    effective_to: date | None = None  # None = point-in-time / open-ended
    knowledge_from: datetime
    provenance_id: ProvenanceId

    @field_validator("field")
    @classmethod
    def _field_nonempty(cls, v: str) -> str:
        if not v or v != v.strip():
            raise ValueError("field mnemonic must be non-empty with no surrounding whitespace")
        return v

    @field_validator("knowledge_from")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("knowledge_from must be timezone-aware")
        # Canonical UTC so store round-trips reproduce identical facts (I5).
        return v.astimezone(UTC)

    @model_validator(mode="after")
    def _effective_ordered(self) -> Fact:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to precedes effective_from")
        return self
