"""Whether each source is still flowing (Phase 1 sustainability).

The counting `treble status` did could only go up. A source that stopped
publishing, changed its URL, quietly started returning an empty document,
or had its free tier withdrawn produced exactly the same output as a
healthy one: a number that no longer changed, on a screen nobody was
diffing against last week's. That is the oldest failure in this repository
— success read from output that could not report failure — applied to the
data supply rather than to a test.

So every adapter now declares how often it expects to have something new,
and this compares that against the ingest log. The declaration is the point:
a cadence that lives in someone's head cannot be checked, and "I think FRED
updates daily" is not a thing software can act on.

Three states, deliberately distinguished, because they need different
responses and render identically if collapsed:

* `NEVER` — declared and configured, but nothing has ever been fetched.
  Nothing is broken; the source has not been wired into a run yet.
* `OVERDUE` — it used to flow and has stopped. This is the one that costs
  you a rebuild months later, and the whole module exists to surface it on
  the day it happens rather than the day someone notices a chart is flat.
* `FRESH` — within its declared cadence.

Sources with no declared cadence report `IRREGULAR` rather than being
assumed daily. A bulk annual file and a tick feed are both legitimate, and
inventing an expectation for them would generate false alarms that teach a
user to ignore the report — which is worse than not having one.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from treble.ingest.registry import all_sources
from treble.store.ingest_log import IngestLog

#: A daily source skipped over a weekend is three days old on Monday and is
#: not broken; one skipped over Easter is five. Doubling the cadence and
#: adding a day absorbs weekends and public holidays without absorbing a
#: genuine multi-week outage, which is the thing worth catching. Stated as a
#: constant rather than buried in the comparison so it can be argued with.
GRACE_MULTIPLIER = 2.0
GRACE_DAYS = 1.0


class Freshness(enum.Enum):
    FRESH = "fresh"
    OVERDUE = "overdue"
    NEVER = "never"
    IRREGULAR = "irregular"


class SourceHealth(BaseModel):
    """One source's supply state."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    freshness: Freshness
    #: None when nothing has ever been fetched from this source.
    last_fetched: datetime | None
    age_days: float | None
    expected_cadence_days: float | None
    payloads: int

    @property
    def overdue_by_days(self) -> float | None:
        """How far past the tolerated age, or None if not overdue."""
        if (
            self.freshness is not Freshness.OVERDUE
            or self.age_days is None
            or self.expected_cadence_days is None
        ):
            return None
        return self.age_days - _tolerance(self.expected_cadence_days)

    def explain(self) -> str:
        """One line a person can act on."""
        if self.freshness is Freshness.NEVER:
            return "never fetched — not wired into a run yet, rather than broken"
        if self.freshness is Freshness.IRREGULAR:
            return f"{self.payloads} payloads; no cadence declared, so staleness is not judged"
        # FRESH and OVERDUE are only reachable with both of these set, but
        # narrowed with a default rather than an assert: asserts vanish
        # under -O, and a status line that raised in an optimised build
        # would take the whole report down with it.
        age = self.age_days or 0.0
        cadence = self.expected_cadence_days or 0.0
        if self.freshness is Freshness.FRESH:
            return f"last {age:.1f}d ago, within its {cadence:g}d cadence"
        return (
            f"last {age:.1f}d ago against a {cadence:g}d cadence — "
            f"{age - _tolerance(cadence):.1f}d past tolerance; check the endpoint before "
            "trusting anything downstream of it"
        )


def _tolerance(cadence_days: float) -> float:
    return cadence_days * GRACE_MULTIPLIER + GRACE_DAYS


def source_health(log: IngestLog, *, now: datetime | None = None) -> tuple[SourceHealth, ...]:
    """Every registered source's supply state, worst first.

    Registered rather than logged: a source that has never run is exactly
    the one most likely to have been forgotten, and reporting only on
    sources that appear in the log would make it invisible.
    """
    at = now or datetime.now(UTC)
    latest: dict[str, datetime] = {}
    counts: dict[str, int] = {}
    for entry in log.read():
        counts[entry.source] = counts.get(entry.source, 0) + 1
        if entry.source not in latest or entry.fetched_at > latest[entry.source]:
            latest[entry.source] = entry.fetched_at

    out: list[SourceHealth] = []
    for source_id, meta in all_sources().items():
        cadence = meta.expected_cadence_days
        last = latest.get(source_id)
        age = None if last is None else (at - last).total_seconds() / 86400.0
        if last is None:
            freshness = Freshness.NEVER
        elif cadence is None:
            freshness = Freshness.IRREGULAR
        elif age is not None and age > _tolerance(cadence):
            freshness = Freshness.OVERDUE
        else:
            freshness = Freshness.FRESH
        out.append(
            SourceHealth(
                source_id=source_id,
                freshness=freshness,
                last_fetched=last,
                age_days=age,
                expected_cadence_days=cadence,
                payloads=counts.get(source_id, 0),
            )
        )

    # Worst first. A report that buried the one broken source among twenty
    # healthy ones alphabetically would be read once and never again.
    order = {
        Freshness.OVERDUE: 0,
        Freshness.NEVER: 1,
        Freshness.IRREGULAR: 2,
        Freshness.FRESH: 3,
    }
    return tuple(sorted(out, key=lambda h: (order[h.freshness], -(h.age_days or 0.0))))


def overdue(health: tuple[SourceHealth, ...]) -> tuple[SourceHealth, ...]:
    """Just the ones that have stopped flowing."""
    return tuple(h for h in health if h.freshness is Freshness.OVERDUE)


__all__ = [
    "GRACE_DAYS",
    "GRACE_MULTIPLIER",
    "Freshness",
    "SourceHealth",
    "overdue",
    "source_health",
]
