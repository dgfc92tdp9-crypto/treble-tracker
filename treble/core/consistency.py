"""Accounting identities that must hold, and what it means when they do not.

In `core`, not `analytics`. I7 forbids presentation code from importing
`treble.analytics`, and the resolver is where an assembled statement first
exists as a *set* of figures rather than a sequence of independent field
calls — so that is where the check has to run. Nothing here computes
anything: it is arithmetic on numbers already fetched, expressing what
double-entry bookkeeping requires, which every layer may ask about.

Spec §14.1 requires it outright: *"a statement that does not foot is
rejected automatically."* This is that check.

It exists because of a specific defect. `DES` and `FA` rendered Apple's
**Q4 FY2018** revenue — 62,900,000,000, from a tag Apple abandoned in 2018 —
under a heading reading "3 months to 2026-03-28", beside a net income that
genuinely was from that quarter. Nothing was corrupt and nothing raised: the
binding asked for the latest value of a tag and got one seven years old.

`BoundCell.period_from` closes that hole and `scripts/check_screen_periods.py`
makes declaring it structural rather than optional. Both are about *where a
number came from*. This module is the other half: whether the numbers on the
screen **agree with each other**, which catches a wrong value even when every
period lines up.

## The identities are chosen by measurement, not intuition

Each was run over the live store on 2026-09-01 and kept only if it holds
almost always. An identity that fails often is not a check — it is noise
that teaches a reader to ignore the column, which is worse than no check at
all (the same argument `ingest.health` makes about cadences it will not
invent).

The measured rates are recorded on each identity below. One candidate was
**discarded** by this process and it is worth stating why, because it is the
obvious one to reach for:

    EPS_basic x weighted shares  vs  NetIncomeLoss                   16.19% break
    EPS_basic x weighted shares  vs  NetIncomeLossAvailableTo...      1.18% break

Earnings per share is computed on income *available to common
shareholders*, which differs from net income wherever preferred dividends
or non-controlling interests exist. The intuitive form of the check is
wrong about one filing in six.

A second candidate, `Assets = Liabilities + StockholdersEquity`, breaks
11.18% of the time and is also excluded. Investigated rather than assumed:
filers restate one leg of the balance sheet without the others, so the
source data itself stops footing. That is a true statement about the
filing, not about this code, and flagging it on every screen would bury the
0.05% case that is worth someone's attention.

## What a violation means

Not that the store is corrupt. The store records what the filer said, and
these identities are checked against exactly that. A violation means **the
figures on this screen do not add up**, and the right response is to show
that rather than to pick one and present it as fact.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

ASSETS = "us-gaap:Assets:USD"
ASSETS_CURRENT = "us-gaap:AssetsCurrent:USD"
LIABILITIES = "us-gaap:Liabilities:USD"
LIABILITIES_CURRENT = "us-gaap:LiabilitiesCurrent:USD"
BALANCE_TOTAL = "us-gaap:LiabilitiesAndStockholdersEquity:USD"
EPS_BASIC = "us-gaap:EarningsPerShareBasic:USD/shares"
SHARES_BASIC = "us-gaap:WeightedAverageNumberOfSharesOutstandingBasic:shares"
INCOME_TO_COMMON = "us-gaap:NetIncomeLossAvailableToCommonStockholdersBasic:USD"


@dataclass(frozen=True)
class Violation:
    """One identity that did not hold, with the arithmetic that shows it."""

    identity: str
    left: float
    right: float
    tolerance: float
    #: The fields whose values produced it, so a renderer can mark exactly
    #: the cells involved rather than the whole screen.
    fields: tuple[str, ...] = ()

    @property
    def gap(self) -> float:
        return self.left - self.right

    @property
    def relative(self) -> float:
        scale = abs(self.right) or abs(self.left)
        return abs(self.gap) / scale if scale else 0.0

    def __str__(self) -> str:
        return (
            f"{self.identity}: {self.left:,.0f} vs {self.right:,.0f} "
            f"({self.relative:.2%} apart, tolerance {self.tolerance:.2%})"
        )


@dataclass(frozen=True)
class Identity:
    """One accounting relationship, its tolerance, and how often it holds.

    ``measured_break_rate`` is not decoration. It is the reason the identity
    is here rather than in the paragraph of the module docstring listing the
    ones that were thrown out, and it is what a later reader needs in order
    to argue with the choice.
    """

    name: str
    left_of: tuple[str, ...]
    right_of: tuple[str, ...]
    tolerance: float
    measured_break_rate: float
    sample: int
    #: True when the left side may legitimately be *below* the right — a
    #: subtotal against its total — rather than equal to it.
    subset: bool = False


#: Tolerances are relative and generous by accounting standards, because
#: what is being caught is a value from the wrong period or the wrong
#: concept — an error of tens of percent — not a rounding difference.
IDENTITIES: tuple[Identity, ...] = (
    Identity(
        name="balance sheet foots",
        left_of=(ASSETS,),
        right_of=(BALANCE_TOTAL,),
        tolerance=0.005,
        measured_break_rate=0.0005,
        sample=35_751,
    ),
    Identity(
        name="current assets within total assets",
        left_of=(ASSETS_CURRENT,),
        right_of=(ASSETS,),
        tolerance=0.005,
        measured_break_rate=0.0001,
        sample=28_682,
        subset=True,
    ),
    Identity(
        name="current liabilities within total liabilities",
        left_of=(LIABILITIES_CURRENT,),
        right_of=(LIABILITIES,),
        tolerance=0.005,
        measured_break_rate=0.0001,
        sample=24_566,
        subset=True,
    ),
)

#: Per-share consistency is kept apart because its left side is a *product*
#: rather than a sum, which the tuple form above cannot express.
EPS_TOLERANCE = 0.02
EPS_BREAK_RATE = 0.0118
EPS_SAMPLE = 255


def _sum(values: Mapping[str, float], fields: tuple[str, ...]) -> float | None:
    total = 0.0
    for field in fields:
        value = values.get(field)
        if value is None:
            return None
        total += value
    return total


def check(values: Mapping[str, float]) -> tuple[Violation, ...]:
    """Every identity that fails on ``values``. Pure — no store, no clock.

    ``values`` is one statement: field name to number, all from the same
    period. An identity whose inputs are absent is skipped rather than
    failed — most filers do not tag everything, and treating a missing tag
    as a broken statement would flag almost every company.
    """
    found: list[Violation] = []
    for identity in IDENTITIES:
        left = _sum(values, identity.left_of)
        right = _sum(values, identity.right_of)
        if left is None or right is None:
            continue
        scale = abs(right) or abs(left)
        if not scale:
            continue
        gap = (left - right) if not identity.subset else max(left - right, 0.0)
        if abs(gap) > identity.tolerance * scale:
            found.append(
                Violation(
                    identity.name,
                    left,
                    right,
                    identity.tolerance,
                    identity.left_of + identity.right_of,
                )
            )

    eps, shares, income = (
        values.get(EPS_BASIC),
        values.get(SHARES_BASIC),
        values.get(INCOME_TO_COMMON),
    )
    if eps is not None and shares is not None and income is not None and income:
        implied = eps * shares
        if abs(implied - income) > EPS_TOLERANCE * abs(income):
            found.append(
                Violation(
                    "earnings per share reconciles",
                    implied,
                    income,
                    EPS_TOLERANCE,
                    (EPS_BASIC, SHARES_BASIC, INCOME_TO_COMMON),
                )
            )
    return tuple(found)


__all__ = [
    "ASSETS",
    "ASSETS_CURRENT",
    "BALANCE_TOTAL",
    "EPS_BASIC",
    "EPS_BREAK_RATE",
    "EPS_SAMPLE",
    "EPS_TOLERANCE",
    "IDENTITIES",
    "INCOME_TO_COMMON",
    "LIABILITIES",
    "LIABILITIES_CURRENT",
    "SHARES_BASIC",
    "Identity",
    "Violation",
    "check",
]
