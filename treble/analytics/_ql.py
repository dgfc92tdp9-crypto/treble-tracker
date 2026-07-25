"""The only module that touches QuantLib global state (CLAUDE.md §5).

Trap 1 — ``ql.Settings.instance().evaluationDate`` is process-global mutable
state. Every QuantLib computation runs inside :func:`evaluation_date`, which
serialises access behind a lock and restores the previous date on exit.
Parallel valuation across as-of dates must use process isolation, never
threads.

Trap 3 — calendars and day counters are expensive to construct; they are
cached here, keyed by their enum.

No other module may import ``QuantLib.Settings`` or mutate evaluation state.
A CI check greps for violations.
"""

from __future__ import annotations

import enum
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from functools import cache

import QuantLib as ql

_settings_lock = threading.RLock()


def to_ql_date(d: date) -> ql.Date:
    return ql.Date(d.day, d.month, d.year)


def from_ql_date(d: ql.Date) -> date:
    return date(d.year(), d.month(), d.dayOfMonth())


@contextmanager
def evaluation_date(as_of: date) -> Iterator[None]:
    """Serialise all QuantLib work behind the process-global evaluation date."""
    with _settings_lock:
        settings = ql.Settings.instance()
        previous = settings.evaluationDate
        settings.evaluationDate = to_ql_date(as_of)
        try:
            yield
        finally:
            settings.evaluationDate = previous


class DayCount(enum.Enum):
    """Day count conventions named in spec §10.1."""

    THIRTY_360 = "30/360"
    ACT_ACT_ICMA = "ACT/ACT ICMA"
    ACT_360 = "ACT/360"
    ACT_365F = "ACT/365F"
    THIRTY_E_360 = "30E/360"
    THIRTY_E_360_ISDA = "30E/360 ISDA"
    ACT_ACT_ISDA = "ACT/ACT ISDA"


class BusinessDay(enum.Enum):
    FOLLOWING = "Following"
    MODIFIED_FOLLOWING = "Modified Following"
    PRECEDING = "Preceding"
    MODIFIED_PRECEDING = "Modified Preceding"
    UNADJUSTED = "Unadjusted"


class Market(enum.Enum):
    US_GOVERNMENT = "us-government"
    US_SETTLEMENT = "us-settlement"
    TARGET = "target"
    UK = "uk"
    JAPAN = "japan"


@cache
def day_counter(convention: DayCount) -> ql.DayCounter:
    match convention:
        case DayCount.THIRTY_360:
            return ql.Thirty360(ql.Thirty360.BondBasis)
        case DayCount.ACT_ACT_ICMA:
            return ql.ActualActual(ql.ActualActual.ISMA)
        case DayCount.ACT_360:
            return ql.Actual360()
        case DayCount.ACT_365F:
            return ql.Actual365Fixed()
        case DayCount.THIRTY_E_360:
            return ql.Thirty360(ql.Thirty360.EurobondBasis)
        case DayCount.THIRTY_E_360_ISDA:
            return ql.Thirty360(ql.Thirty360.ISDA, ql.Date())
        case DayCount.ACT_ACT_ISDA:
            return ql.ActualActual(ql.ActualActual.ISDA)


@cache
def calendar(market: Market) -> ql.Calendar:
    match market:
        case Market.US_GOVERNMENT:
            return ql.UnitedStates(ql.UnitedStates.GovernmentBond)
        case Market.US_SETTLEMENT:
            return ql.UnitedStates(ql.UnitedStates.Settlement)
        case Market.TARGET:
            return ql.TARGET()
        case Market.UK:
            return ql.UnitedKingdom()
        case Market.JAPAN:
            return ql.Japan()


@cache
def business_day(convention: BusinessDay) -> int:
    match convention:
        case BusinessDay.FOLLOWING:
            return int(ql.Following)
        case BusinessDay.MODIFIED_FOLLOWING:
            return int(ql.ModifiedFollowing)
        case BusinessDay.PRECEDING:
            return int(ql.Preceding)
        case BusinessDay.MODIFIED_PRECEDING:
            return int(ql.ModifiedPreceding)
        case BusinessDay.UNADJUSTED:
            return int(ql.Unadjusted)
