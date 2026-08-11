"""`ECO` — the macro dashboard (spec §7.4).

Thirty-six FRED series are ingested and refreshed daily. Eleven of them are
the DGS curve points `ICVS` draws. The other twenty-five — inflation,
labour, credit spreads, breakevens, the 2s10s slope, policy and overnight
rates, equity levels and vol — had **no screen at all**. Data arriving on a
timer that nothing displays is the same defect as an analytic nothing can
call, running in the other direction, and this repository has now found it
in both.

Two things make a macro table honest, and neither is optional:

**Units.** `CPIAUCSL` is 332.568 and `UNRATE` is 4.1. One is an index on a
1982-84 base, the other a percentage of the labour force. A column of bare
numbers invites reading the first as a percentage and the second as an
index, and nothing on the screen would contradict either reading. So every
row carries its unit, and the catalogue below is the only place a unit is
written down.

**The observation date.** These series do not arrive on the same clock. On
2026-08-11 this store held CPI for June, unemployment for July, VIX for the
7th and the 2s10s slope for the 10th — a spread of ten weeks across four
rows of one table. A dashboard showing "latest" without saying *as of when*
presents a June inflation print beside a Monday vol close as though a
reader could compare them.

Staleness is judged per series against its own release frequency, for the
reason `ingest.health` judges sources against their own cadence: monthly
CPI six weeks old is a normal publication lag, and a daily series six weeks
old is a broken feed. One threshold would call the first broken or the
second fine.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date, datetime

from treble.core.identifiers import TUID
from treble.store.duck import DuckStore


class Frequency(enum.Enum):
    """How often a series is published, and thus when silence is a fault."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"

    @property
    def tolerated_days(self) -> int:
        """How old the newest observation may be before it reads as stale.

        Generous, and deliberately so. These are publication lags, not feed
        health: monthly CPI is released about a fortnight after the month it
        covers, so a June print is routinely six weeks old in early August
        and nothing is wrong. A threshold tight enough to flag that would
        flag it every month, and a warning that is always on is furniture.
        """
        return {
            Frequency.DAILY: 7,
            Frequency.WEEKLY: 21,
            Frequency.MONTHLY: 75,
            Frequency.QUARTERLY: 210,
        }[self]


@dataclass(frozen=True)
class MacroSeries:
    """One series' reference data: what it is, and in what units."""

    series_id: str
    title: str
    unit: str
    frequency: Frequency
    group: str


#: The catalogue. Written here rather than fetched because FRED's series
#: metadata endpoint needs an API key while the observations do not, and a
#: table of twenty-five titles is not worth a credential that can expire.
#: It is reference data about a known set, not invented data: every unit
#: below is FRED's own stated unit for that series.
CATALOGUE: tuple[MacroSeries, ...] = (
    # -- rates and policy ------------------------------------------------
    MacroSeries("DFF", "Fed funds effective rate", "%", Frequency.DAILY, "rates"),
    MacroSeries("SOFR", "Secured overnight financing rate", "%", Frequency.DAILY, "rates"),
    MacroSeries("T10Y2Y", "10-year minus 2-year Treasury", "% points", Frequency.DAILY, "rates"),
    # -- inflation and expectations --------------------------------------
    MacroSeries(
        "CPIAUCSL", "CPI, all urban consumers", "index 1982-84=100", Frequency.MONTHLY, "inflation"
    ),
    MacroSeries(
        "CPILFESL",
        "Core CPI, ex food and energy",
        "index 1982-84=100",
        Frequency.MONTHLY,
        "inflation",
    ),
    MacroSeries("PCEPI", "PCE price index", "index 2017=100", Frequency.MONTHLY, "inflation"),
    MacroSeries(
        "PCEPILFE", "Core PCE price index", "index 2017=100", Frequency.MONTHLY, "inflation"
    ),
    MacroSeries("T10YIE", "10-year breakeven inflation", "%", Frequency.DAILY, "inflation"),
    MacroSeries("T5YIFR", "5y5y forward inflation expectation", "%", Frequency.DAILY, "inflation"),
    # -- activity and labour ---------------------------------------------
    MacroSeries("UNRATE", "Unemployment rate", "%", Frequency.MONTHLY, "activity"),
    MacroSeries(
        "PAYEMS", "Nonfarm payrolls", "thousands of persons", Frequency.MONTHLY, "activity"
    ),
    MacroSeries("ICSA", "Initial jobless claims", "persons", Frequency.WEEKLY, "activity"),
    MacroSeries("GDPC1", "Real GDP", "USD bn, 2017 chained", Frequency.QUARTERLY, "activity"),
    MacroSeries("M2SL", "M2 money stock", "USD bn", Frequency.MONTHLY, "activity"),
    # -- credit ------------------------------------------------------------
    MacroSeries("BAMLC0A0CM", "US corporate OAS", "% points", Frequency.DAILY, "credit"),
    MacroSeries("BAMLC0A4CBBB", "US BBB corporate OAS", "% points", Frequency.DAILY, "credit"),
    MacroSeries("BAMLH0A0HYM2", "US high yield OAS", "% points", Frequency.DAILY, "credit"),
    # -- equity, vol and FX ------------------------------------------------
    MacroSeries("SP500", "S&P 500", "index", Frequency.DAILY, "markets"),
    MacroSeries("DJIA", "Dow Jones industrial average", "index", Frequency.DAILY, "markets"),
    MacroSeries("NASDAQCOM", "Nasdaq composite", "index", Frequency.DAILY, "markets"),
    MacroSeries("NASDAQ100", "Nasdaq 100", "index", Frequency.DAILY, "markets"),
    MacroSeries("VIXCLS", "CBOE volatility index", "index", Frequency.DAILY, "markets"),
    MacroSeries("DTWEXBGS", "Broad dollar index", "index Jan 2006=100", Frequency.DAILY, "markets"),
    MacroSeries("DEXUSEU", "USD per EUR", "USD", Frequency.DAILY, "markets"),
    MacroSeries("DEXJPUS", "JPY per USD", "JPY", Frequency.DAILY, "markets"),
)

GROUPS: tuple[str, ...] = ("rates", "inflation", "activity", "credit", "markets")


@dataclass(frozen=True)
class MacroReading:
    """One series as the dashboard shows it."""

    series: MacroSeries
    #: None when the store holds the series but no observation is readable
    #: at this `as_of` — distinct from a series never ingested at all.
    value: float | None
    observed: date | None
    #: The previous observation, for the change column. `None` where the
    #: series has only ever printed once.
    previous: float | None
    ingested: bool

    @property
    def change(self) -> float | None:
        if self.value is None or self.previous is None:
            return None
        return self.value - self.previous

    def staleness(self, *, today: date) -> str:
        """Empty when current, else how far past its own release lag.

        Judged against the series' own frequency. A single threshold would
        call routine monthly publication lag a fault, or let a dead daily
        feed pass — and the first is worse, because a warning that is always
        on stops being read.
        """
        if not self.ingested:
            return "not ingested"
        if self.observed is None:
            return "no observation"
        age = (today - self.observed).days
        if age <= self.series.frequency.tolerated_days:
            return ""
        return f"stale: {age}d for a {self.series.frequency.value} series"


def macro_dashboard(
    store: DuckStore, *, as_of: datetime, group: str | None = None
) -> tuple[MacroReading, ...]:
    """Every catalogued series' latest reading, point-in-time (I2).

    Series in the catalogue that the store has never ingested are returned
    with `ingested=False` rather than dropped. A dashboard that silently
    omitted them would make a configuration gap — nobody ever fetched this
    series — look identical to a series that simply has no print today, and
    only one of those is worth acting on.
    """
    out: list[MacroReading] = []
    for series in CATALOGUE:
        if group is not None and series.group != group:
            continue
        facts = store.read(TUID(f"fred:{series.series_id}"), "PX_LAST", as_of=as_of)
        readings = sorted(
            ((f.effective_from, float(f.value)) for f in facts if isinstance(f.value, float | int)),
            key=lambda pair: pair[0],
        )
        out.append(
            MacroReading(
                series=series,
                value=readings[-1][1] if readings else None,
                observed=readings[-1][0] if readings else None,
                previous=readings[-2][1] if len(readings) > 1 else None,
                ingested=bool(facts),
            )
        )
    return tuple(out)


__all__ = [
    "CATALOGUE",
    "GROUPS",
    "Frequency",
    "MacroReading",
    "MacroSeries",
    "macro_dashboard",
]
