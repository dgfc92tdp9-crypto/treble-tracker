"""Bond term structures as typed, frozen inputs to the pricing functions.

These mirror the security-master fields for a fixed coupon bond (spec §9.4);
resolvers construct them from stored terms. Nothing here fabricates data —
every instance comes from stored, provenance-carrying terms or from clearly
marked test fixtures.
"""

from __future__ import annotations

import enum
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from treble.analytics._ql import BusinessDay, DayCount, Market


class Frequency(enum.Enum):
    ANNUAL = 1
    SEMIANNUAL = 2
    QUARTERLY = 4
    MONTHLY = 12


class CallSchedule(BaseModel):
    """One call (or put) right: exercisable on/after ``start`` at ``price``."""

    model_config = ConfigDict(frozen=True)

    start: date
    price: float = Field(gt=0.0)


class FixedBondSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    face: float = 100.0
    coupon: float = Field(ge=0.0)  # annual rate, e.g. 0.0415
    frequency: Frequency = Frequency.SEMIANNUAL
    issue_date: date
    maturity: date
    day_count: DayCount = DayCount.THIRTY_360
    calendar: Market = Market.US_GOVERNMENT
    business_day: BusinessDay = BusinessDay.FOLLOWING
    settlement_days: int = Field(ge=0, default=2)
    calls: tuple[CallSchedule, ...] = ()  # empty = bullet
    puts: tuple[CallSchedule, ...] = ()

    @model_validator(mode="after")
    def _dates_ordered(self) -> FixedBondSpec:
        if self.maturity <= self.issue_date:
            raise ValueError("maturity must follow issue date")
        for right in (*self.calls, *self.puts):
            if not (self.issue_date < right.start <= self.maturity):
                raise ValueError("exercise start outside bond life")
        # Call schedules must be monotonic in date (CLAUDE.md §9.4 consistency rules).
        for schedule in (self.calls, self.puts):
            starts = [r.start for r in schedule]
            if starts != sorted(starts):
                raise ValueError("exercise schedule not monotonic in date")
        return self
