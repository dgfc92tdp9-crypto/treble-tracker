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
    #: Tenor basis swap: float-vs-float, quoted as the spread on the shorter
    #: index. This is the instrument that connects one forecast curve to
    #: another (spec §11.1) — without it a 6M curve built beside a 3M curve
    #: is two unrelated objects rather than a basis.
    BASIS = "basis"


_TENOR_RE = re.compile(r"^\d+[DWMY]$")

#: Floating payments per year by index tenor (see CurveConfig.index_frequency).
_FREQUENCY_BY_INDEX_TENOR: dict[str, int] = {"1M": 12, "3M": 4, "6M": 2, "12M": 1, "1Y": 1}


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
    #: Hull-White mean reversion for the convexity adjustment.
    #:
    #: **Declared, hashed, and not yet read by any bootstrap.** Found by
    #: sweeping for computed members with no reader. It enters
    #: `content_hash` like every other field, so two configs differing only
    #: in this value hash differently and build identically — a false cache
    #: miss, and a false "different model" claim in the I3 envelope.
    #:
    #: Latent rather than live, because it defaults to None and nothing in
    #: this repository sets it. It becomes a real defect the moment somebody
    #: does. Left in place rather than deleted because removing a field
    #: changes every stored curve hash, which is a migration and not a
    #: cleanup; `tests/analytics/curves/test_config.py` pins the situation
    #: so the next person meets it deliberately.
    convexity_mean_reversion: float | None = None
    discount_basis: str = "self"  # "self" = single-curve; a curve name = multi-curve
    #: The index tenor this curve forecasts ("3M", "6M"), or None for a
    #: discounting/overnight curve. Part of the identity because a 3M-index
    #: and a 6M-index curve built from the *same* instrument selection and
    #: the *same* quotes produce different forwards — the float leg pays at
    #: a different frequency. Leaving it out would let two genuinely
    #: different curves share a content hash unless someone remembered to
    #: name them differently, which puts the identity in a display string
    #: (ADR-0006).
    index_tenor: str | None = None
    #: Day counts of the swap instruments' own legs. A USD swap accrues
    #: 30/360 fixed against ACT/360 floating; the curve's `day_count` is the
    #: zero-rate convention and is a different thing. Leaving these None
    #: falls back to `day_count`, which is what the single-curve Phase 1
    #: bootstrap did — preserved so existing curves are unchanged, but a
    #: curve meant to reprice *market* swaps must set them, or its par
    #: rates will disagree with the market's by the ratio between the
    #: conventions (365/360 is 1.4%, which on a 4% rate is 5bp).
    fixed_leg_day_count: DayCount | None = None
    float_leg_day_count: DayCount | None = None
    #: Fixed payments per year on the curve's swap instruments. USD swaps
    #: pay semiannual fixed against quarterly floating; EUR pays annual
    #: against semiannual. Defaults to annual, which is what the Phase 1
    #: single-curve bootstrap assumed.
    swap_fixed_frequency: int = Field(default=1, gt=0)

    @property
    def fixed_leg_convention(self) -> DayCount:
        return self.fixed_leg_day_count or self.day_count

    @property
    def float_leg_convention(self) -> DayCount:
        return self.float_leg_day_count or self.day_count

    @field_validator("index_tenor")
    @classmethod
    def _index_tenor_form(cls, v: str | None) -> str | None:
        if v is None:
            return None
        candidate = v.strip().upper()
        if not _TENOR_RE.match(candidate):
            raise ValueError(f"index tenor must look like 3M / 6M, got {v!r}")
        return candidate

    @property
    def index_frequency(self) -> int:
        """Floating payments per year, from the index tenor.

        Derived rather than configurable. If a swap could state its float
        frequency independently of the curve projecting it, a 3M curve
        could be read on a semiannual schedule — every forward would be
        taken over the wrong period, and the resulting PV would look
        completely normal.

        Restricted to tenors a swap market actually quotes: an index whose
        tenor does not divide the year has no regular schedule, and
        inventing one produces forwards belonging to no traded instrument.
        """
        if self.index_tenor is None:
            raise ValueError(f"{self.name!r} forecasts no index, so it has no floating frequency")
        frequency = _FREQUENCY_BY_INDEX_TENOR.get(self.index_tenor)
        if frequency is None:
            raise ValueError(
                f"no standard schedule for a {self.index_tenor!r} index; supported: "
                + ", ".join(sorted(_FREQUENCY_BY_INDEX_TENOR))
            )
        return frequency

    @property
    def content_hash(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()
