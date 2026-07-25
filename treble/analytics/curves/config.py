"""CurveConfig — invariant I4 (CLAUDE.md §1, spec §11.1 SWDF).

A curve is defined by its instrument selection, interpolation method,
convexity adjustment, and discounting basis. The configuration is frozen and
content-addressed: the hash is stamped into every I3 envelope computed from
the curve, so any downstream number identifies exactly which curve
construction produced it.
"""

from __future__ import annotations

import enum
import hashlib
import json
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from treble.analytics._ql import DayCount, Market


class Interpolation(enum.Enum):
    """Selectable interpolation methods (spec §11.1.4).

    MONOTONE_CONVEX is the default: it cannot produce the oscillating or
    negative forwards that cubic splines on zeros can (ADR-0002).
    """

    LINEAR_ZERO = "linear-zero"
    LOGLINEAR_DISCOUNT = "loglinear-discount"  # == piecewise-constant forwards
    NATURAL_CUBIC_ZERO = "natural-cubic-zero"
    MONOTONIC_CUBIC_ZERO = "monotonic-cubic-zero"  # QuantLib Hyman filter
    MONOTONE_CONVEX = "monotone-convex"  # Hagan-West, in-repo (ADR-0002)


class InstrumentKind(enum.Enum):
    DEPOSIT = "deposit"
    OIS = "ois"
    SWAP = "swap"


_TENOR_RE = re.compile(r"^\d+[DWMY]$")


class InstrumentSpec(BaseModel):
    """One instrument populating a section of the curve (SWDF row).

    The spec selects *which* instruments define the curve; their market
    quotes arrive separately at build time. Same config + different quotes =
    same content hash, different curve values — which is exactly right.
    """

    model_config = ConfigDict(frozen=True)

    kind: InstrumentKind
    tenor: str  # "3M", "10Y"

    @field_validator("tenor")
    @classmethod
    def _tenor_form(cls, v: str) -> str:
        candidate = v.strip().upper()
        if not _TENOR_RE.match(candidate):
            raise ValueError(f"tenor must look like 3M / 10Y, got {v!r}")
        return candidate


class CurveConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str  # e.g. "USD-SOFR-OIS"
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    instruments: tuple[InstrumentSpec, ...] = Field(min_length=1)
    interpolation: Interpolation = Interpolation.MONOTONE_CONVEX
    day_count: DayCount = DayCount.ACT_365F
    calendar: Market = Market.US_GOVERNMENT
    settlement_days: int = Field(ge=0, default=2)
    # Futures convexity adjustment (spec §11.1.2): displayed, not buried.
    # None = no futures in the selection; a float = Hull-White mean reversion
    # parameter used by the adjustment when futures arrive (Phase 2 widens this).
    convexity_mean_reversion: float | None = None
    discount_basis: str = "self"  # "self" = single-curve; a curve name = multi-curve

    @property
    def content_hash(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()
