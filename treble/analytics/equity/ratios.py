"""Equity ratios from as-reported fundamentals (spec §14.1).

5,746 filers are loaded and nothing was derived from them: the fundamentals
were displayed as filed and no margin, return or leverage measure existed.
This is the first pass at that, and it is deliberately small — every
function here is one division with the guards that division needs.

**Every refusal below is a number that would otherwise look reasonable.**
A ratio is a division, and division is the most reliable way to produce a
confident, plausible, wrong figure in finance:

- Zero denominators produce infinity, which renders as a number.
- Negative equity produces a *positive* return on equity when earnings are
  also negative, which reads as a company doing well while it is insolvent.
- A quarterly numerator over an annual denominator produces a ratio roughly
  a quarter of the truth, and nothing about the result says so.

Each is refused explicitly rather than smoothed over, because a dash is
visibly missing and a wrong ratio is not.

Inputs are plain numbers, not store reads: the analytics layer sits below
the store in the architecture, and keeping these pure is what lets them be
tested against worked examples.
"""

from __future__ import annotations

from treble.analytics.registry import model


class UndefinedRatioError(ValueError):
    """The ratio has no meaning for these inputs.

    Raised rather than returning NaN or None so a caller cannot accidentally
    format it as a value. The screens turn this into an em dash.
    """


def _ratio(numerator: float, denominator: float, *, what: str) -> float:
    if denominator == 0:
        raise UndefinedRatioError(f"{what}: denominator is zero")
    return numerator / denominator


@model(
    model_id="equity.gross_margin",
    version="1.0",
    spec_section="§14.1",
    summary="Gross profit as a fraction of revenue",
)
def gross_margin(gross_profit: float, revenue: float) -> float:
    return _ratio(gross_profit, revenue, what="gross margin")


@model(
    model_id="equity.operating_margin",
    version="1.0",
    spec_section="§14.1",
    summary="Operating income as a fraction of revenue",
)
def operating_margin(operating_income: float, revenue: float) -> float:
    return _ratio(operating_income, revenue, what="operating margin")


@model(
    model_id="equity.net_margin",
    version="1.0",
    spec_section="§14.1",
    summary="Net income as a fraction of revenue",
)
def net_margin(net_income: float, revenue: float) -> float:
    return _ratio(net_income, revenue, what="net margin")


@model(
    model_id="equity.return_on_equity",
    version="1.0",
    spec_section="§14.1",
    summary="Net income over shareholders' equity; undefined when equity is negative",
)
def return_on_equity(net_income: float, equity: float) -> float:
    """Return on equity, refused when equity is negative.

    With negative equity the arithmetic still works and the answer is
    actively misleading: a loss-making company with a deficit reports a
    *positive* ROE, which reads as profitability. Boeing and Starbucks have
    both carried negative equity while unprofitable. The ratio is not small
    or unusual there — it is meaningless, and the only honest output is a
    refusal.
    """
    if equity < 0:
        raise UndefinedRatioError(
            "return on equity: equity is negative, so the ratio inverts its own sign "
            "and reads as profitability"
        )
    return _ratio(net_income, equity, what="return on equity")


@model(
    model_id="equity.return_on_assets",
    version="1.0",
    spec_section="§14.1",
    summary="Net income over total assets",
)
def return_on_assets(net_income: float, assets: float) -> float:
    if assets < 0:
        raise UndefinedRatioError("return on assets: assets cannot be negative")
    return _ratio(net_income, assets, what="return on assets")


@model(
    model_id="equity.leverage",
    version="1.0",
    spec_section="§14.1",
    summary="Assets over equity; undefined when equity is negative",
)
def leverage(assets: float, equity: float) -> float:
    if equity < 0:
        raise UndefinedRatioError("leverage: equity is negative; the multiple is not meaningful")
    return _ratio(assets, equity, what="leverage")


@model(
    model_id="equity.book_value_per_share",
    version="1.0",
    spec_section="§14.1",
    summary="Shareholders' equity per share outstanding",
)
def book_value_per_share(equity: float, shares: float) -> float:
    if shares <= 0:
        raise UndefinedRatioError("book value per share: share count must be positive")
    return equity / shares


@model(
    model_id="equity.growth",
    version="1.0",
    spec_section="§14.1",
    summary="Period-on-period growth; undefined across a sign change",
)
def growth(current: float, prior: float) -> float:
    """Growth from ``prior`` to ``current``.

    Refused when the base is zero or the two straddle zero. A move from
    -100 to +50 is not "150% growth" in any useful sense: the percentage
    formula returns -1.5, whose sign says the opposite of what happened.
    Turnarounds are exactly the situation a reader most needs told plainly,
    and a ratio cannot tell them.
    """
    if prior == 0:
        raise UndefinedRatioError("growth: prior period is zero")
    if (prior < 0) != (current < 0):
        raise UndefinedRatioError(
            "growth: the periods straddle zero, so the percentage change misstates "
            "its own direction"
        )
    return (current - prior) / abs(prior)
