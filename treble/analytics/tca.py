"""Execution quality against the benchmarks this install can actually price.

Spec §18.5 wants `TCA` measured against **arrival price, VWAP, close, and
implementation shortfall**. This install can compute one of those four, and
the value of this module is as much in the three it refuses as in the one it
returns.

| benchmark | status | why |
|---|---|---|
| close | **computed** | 885k daily closes from `twelvedata` |
| VWAP | refused | needs intraday volume; not held for any instrument |
| arrival | refused | needs a price at the order's arrival instant, and no
  order record exists — an execution knows its `ClOrdID`, and nothing stores
  the order it belongs to |
| implementation shortfall | refused | measured *from* arrival, so it
  inherits arrival's blocker |

The refusals are typed, not documented. `PMS` learned this the expensive way
— a rule that cannot be evaluated must say `NOT EVALUABLE` rather than pass,
because a compliance report showing green for a rule nobody could check is
worse than one showing an error. A TCA report that quietly omitted VWAP
would read as an execution measured against three benchmarks that agreed.

## Sign convention, which is the part that goes wrong quietly

Slippage is **cost**: positive is worse. A buy filled above the benchmark
paid too much; a sell filled below it received too little. The same absolute
difference is a cost in one direction and a saving in the other, so the side
is not optional and there is no sensible default — a benchmark computed
without it is right half the time and never announces which half.

Reported in basis points of the benchmark, because a 2-cent difference on a
$3 stock and on a $300 stock are not the same execution.

## What "close" is worth as a benchmark

Less than arrival, and this is not a hedged claim. Comparing a fill to the
day's close measures the fill against a price struck hours later, so a large
order that moved the market is scored against the market it moved. It is a
real and widely used benchmark — it is what a daily-data install can honestly
compute — but it is not a measure of the trader's decision, and
`CloseBenchmark.caveat` says so wherever it is displayed rather than only
here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from treble.analytics.registry import model

#: Basis points per unit. Stated once so a factor of ten cannot appear in
#: one formula and not another.
BASIS_POINTS = 10_000.0

#: Sides that pay to buy. Anything else receives, and the sign flips.
#: Derived from the same `SIDES` mapping `ems.executions` writes, rather
#: than re-listing FIX codes here — a second list would be a second thing
#: to keep in step, and the direction of a cost is not worth that risk.
BUY_SIDES = frozenset({"buy"})


class BenchmarkUnavailableError(ValueError):
    """The benchmark cannot be computed from what this install holds.

    A distinct type rather than a None return, for the reason the PMS
    engine has two refusal types: a caller must be unable to treat "no
    slippage" and "no benchmark" as the same answer. Zero slippage is an
    excellent execution; an unavailable benchmark is no execution quality
    information at all.
    """


@dataclass(frozen=True)
class CloseBenchmark:
    """An execution measured against the closing price of its trade date."""

    symbol: str
    trade_date: date
    side: str
    executed_price: float
    close_price: float
    quantity: float
    #: What the series actually is — `ADJ_CLOSE` and `PX_LAST` are different
    #: benchmarks, and one adjusted for a split that fell between the trade
    #: and today would score the fill against a price the market never saw.
    basis: str

    @property
    def slippage_bp(self) -> float:
        """Cost in basis points of the close. Positive is worse."""
        return _slippage_bp(
            executed=self.executed_price, benchmark=self.close_price, side=self.side
        )

    @property
    def cost(self) -> float:
        """Slippage as money, in the instrument's currency.

        Quantity times the per-unit difference, signed the same way as
        `slippage_bp` so a positive number is money lost against the
        benchmark on both.
        """
        difference = self.executed_price - self.close_price
        return self.quantity * (difference if self.side in BUY_SIDES else -difference)

    @property
    def caveat(self) -> str:
        """Printed wherever this is displayed, not just documented here."""
        return (
            "measured against the close, struck hours after the fill: a large order "
            "is scored against the market it moved. Not a measure of the decision."
        )


def _slippage_bp(*, executed: float, benchmark: float, side: str) -> float:
    if benchmark <= 0:
        raise BenchmarkUnavailableError(
            f"benchmark price {benchmark} is not positive; basis points of it are meaningless"
        )
    difference = (executed - benchmark) / benchmark * BASIS_POINTS
    return difference if side in BUY_SIDES else -difference


@model(
    model_id="tca.close",
    version="1",
    spec_section="18.5",
    summary="Execution slippage against the close of the trade date, in basis points",
)
def close_benchmark(
    *,
    symbol: str,
    trade_date: date,
    side: str,
    executed_price: float,
    quantity: float,
    closes: dict[date, float],
    basis: str,
) -> CloseBenchmark:
    """Measure one fill against its trade date's close.

    ``closes`` is passed in rather than fetched, so this stays a pure
    function of its arguments — the same contract every pricer here holds,
    and what lets it be tested without a store.

    **The close for the trade date, never the nearest one.** Substituting a
    neighbouring day's close would silently score a Friday fill against
    Monday's price, and a missing close is a fact about the data worth
    surfacing rather than papering over: a benchmark quietly taken from
    another day is indistinguishable from a correct one in every report it
    reaches.
    """
    if executed_price <= 0:
        raise BenchmarkUnavailableError(
            f"{symbol}: executed price {executed_price} is not positive"
        )
    close = closes.get(trade_date)
    if close is None:
        raise BenchmarkUnavailableError(
            f"{symbol}: no close for {trade_date}. The series holds "
            f"{len(closes)} day(s); a neighbouring close would score this fill "
            "against a price struck on a different day."
        )
    return CloseBenchmark(
        symbol=symbol,
        trade_date=trade_date,
        side=side,
        executed_price=executed_price,
        close_price=close,
        quantity=quantity,
        basis=basis,
    )


#: Benchmarks §18.5 names that this install cannot compute, and the reason
#: each is blocked. Read by `tapi.tca` so a screen prints them beside the
#: one number it has, rather than presenting that number as "the" TCA.
#:
#: A mapping rather than prose because it is displayed: a caveat that lives
#: only in a docstring is a caveat the person reading the report never sees.
UNAVAILABLE: dict[str, str] = {
    "vwap": (
        "needs intraday volume. No instrument in this store has it — the daily "
        "series carry one price per day and volume-weighting one point is the point."
    ),
    "arrival": (
        "needs a price at the instant the order arrived, and no order record exists: "
        "an execution carries its ClOrdID and nothing stores the order it belongs to. "
        "Blocked on the order store before it is blocked on intraday data."
    ),
    "implementation_shortfall": (
        "measured from the arrival price, so it inherits arrival's blocker. "
        "Building it on the close instead would produce a number with the right "
        "name and the wrong meaning."
    ),
}


__all__ = [
    "BASIS_POINTS",
    "BUY_SIDES",
    "UNAVAILABLE",
    "BenchmarkUnavailableError",
    "CloseBenchmark",
    "close_benchmark",
]
