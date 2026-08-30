"""Execution quality, joined from the book to the price series (§18.5).

The two halves exist separately and neither is useful alone: `ems.executions`
records what was filled, `tapi.prices` holds the daily series, and TCA is the
join. This is that join, and it lives in TAPI because I7 says every screen
reads through TAPI or the data path forks.

**What comes back is mostly refusals, and that is the honest shape.** One of
§18.5's four benchmarks is computable here; a report presenting that one
number without the other three would read as an execution measured against a
panel of agreeing benchmarks. `unavailable` carries the three and their
reasons so a screen prints them beside the number rather than instead of it.

A fill whose close is missing is also a refusal rather than an omission.
Dropping it would shrink the denominator of every average silently — the
executions that could not be measured are exactly the ones on days the price
series does not cover, which is not a random sample.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from treble.analytics.tca import (
    BUY_SIDES,
    UNAVAILABLE,
    BenchmarkUnavailableError,
    CloseBenchmark,
    close_benchmark,
)
from treble.core.identifiers import TUID
from treble.ems.executions import EXECUTION_PREFIX
from treble.ems.orders import ORDER_PREFIX
from treble.store.duck import DuckStore
from treble.tapi.prices import NoPriceSeriesError, listing_subject, price_series

#: Fields read back off an execution subject. A subset of what
#: `ems.executions` writes: the benchmark needs the symbol, side, price,
#: quantity and date, and reading the rest would be reading fields nothing
#: uses — which the unread-members gate would rightly ask about.
_SYMBOL = "ems:exec:symbol"
_SIDE = "ems:exec:side"
_LAST_PX = "ems:exec:lastPx"
_LAST_QTY = "ems:exec:lastQty"
_ORDER_ID = "ems:exec:orderId"
_ORDER_QTY = "ems:order:quantity"
_ORDER_LIMIT = "ems:order:limitPrice"


def unavailable_reason(benchmark: str) -> str:
    """Why a named §18.5 benchmark is not computed on this install.

    Raises on an unknown name rather than returning a vague string: a typo
    in a screen would otherwise render as a plausible-looking explanation
    for a benchmark that does not exist, which is worse than a blank.

    Called by `execution_quality` for every name it reports, so the check
    runs in production rather than only under test.
    """
    try:
        return UNAVAILABLE[benchmark]
    except KeyError:
        raise KeyError(
            f"{benchmark!r} is not a benchmark this module knows about; "
            f"expected one of {sorted(UNAVAILABLE)}"
        ) from None


#: The three of §18.5's four this install cannot compute. Listed rather than
#: taken as `UNAVAILABLE.keys()` so the report's shape is stated here and a
#: name appearing in the mapping without being reported is a visible
#: difference rather than a silent one.
NOT_COMPUTED = ("arrival", "implementation_shortfall", "vwap")


@dataclass(frozen=True)
class Unmeasured:
    """A fill that could not be scored, and why.

    Kept beside the measured ones rather than dropped: the executions with
    no close are those on days the series does not cover, which is not a
    random sample of the book, so silently excluding them biases every
    average computed over what remains.
    """

    exec_id: str
    symbol: str
    trade_date: date
    reason: str


@dataclass(frozen=True)
class OrderOutcome:
    """What an order asked for, and what it got.

    The order store's payoff. Slippage against the close says how the fill
    compared to *the market*; this says how it compared to **the decision**
    — the limit the trader actually set, and how much of the order that
    limit managed to fill.

    Not an arrival-price benchmark, and deliberately not named like one.
    Arrival needs a price at the order's instant and this install holds one
    close per day; the limit is a number the trader chose, which is a
    different and weaker claim honestly stated.
    """

    order_id: str
    symbol: str
    side: str
    ordered: float
    filled: float
    limit_price: float
    average_price: float | None

    @property
    def completion(self) -> float:
        """Fraction of the order that filled, in [0, 1]."""
        return self.filled / self.ordered if self.ordered else 0.0

    @property
    def price_improvement_bp(self) -> float | None:
        """Basis points better than the limit. Positive is better.

        A buy filled below its limit did better than the trader required;
        a sell filled above it did. Signed the opposite way from slippage
        because the limit is a *worst acceptable* price rather than a
        benchmark to beat, so being inside it is the good direction — and
        the two are reported side by side, so one convention for both
        would make one of them read backwards.
        """
        if self.average_price is None or self.limit_price <= 0:
            return None
        difference = (self.limit_price - self.average_price) / self.limit_price * 10_000.0
        return difference if self.side in BUY_SIDES else -difference


@dataclass(frozen=True)
class ExecutionQuality:
    """Every fill in the book, measured where it could be."""

    measured: tuple[CloseBenchmark, ...]
    unmeasured: tuple[Unmeasured, ...]
    #: One row per order that has a record, joined to its fills by ClOrdID.
    #: Empty on a book whose orders predate the order store — the fills are
    #: still measured against the close, and their orders simply are not
    #: there to compare against.
    orders: tuple[OrderOutcome, ...]
    #: §18.5 benchmarks this install cannot compute, and why. Carried in
    #: the result so a screen cannot render the measured number alone.
    unavailable: dict[str, str]

    @property
    def fills(self) -> int:
        return len(self.measured) + len(self.unmeasured)

    @property
    def average_slippage_bp(self) -> float | None:
        """Mean slippage over the measured fills, or None if none were.

        Unweighted, and named for it. A quantity-weighted average is the
        more useful number and belongs beside this one rather than
        replacing it, because the two answer different questions: this is
        how the average fill did, weighted is how the money did.
        """
        if not self.measured:
            return None
        return sum(b.slippage_bp for b in self.measured) / len(self.measured)

    @property
    def total_cost(self) -> float:
        """Money lost against the close, summed. Positive is cost."""
        return sum(b.cost for b in self.measured)


def _value(store: DuckStore, subject: TUID, field: str, *, as_of: datetime) -> object | None:
    facts = store.read(subject, field, as_of=as_of)
    return facts[0].value if facts else None


def execution_quality(store: DuckStore, *, as_of: datetime) -> ExecutionQuality:
    """Score every recorded fill against the close of its trade date.

    Returns an empty result on an install that has never traded, rather
    than raising: no executions is a true and unremarkable state, and a
    screen showing "no fills recorded" is right where one showing an error
    would be misleading.
    """
    measured: list[CloseBenchmark] = []
    unmeasured: list[Unmeasured] = []

    for subject in store.subjects_with_prefix(EXECUTION_PREFIX, as_of=as_of):
        symbol = _value(store, subject, _SYMBOL, as_of=as_of)
        side = _value(store, subject, _SIDE, as_of=as_of)
        price = _value(store, subject, _LAST_PX, as_of=as_of)
        quantity = _value(store, subject, _LAST_QTY, as_of=as_of)
        exec_id = str(subject).removeprefix(EXECUTION_PREFIX)
        if not isinstance(symbol, str) or not isinstance(side, str):
            unmeasured.append(
                Unmeasured(
                    exec_id=exec_id,
                    symbol=str(symbol),
                    trade_date=as_of.date(),
                    reason="the execution carries no symbol or side",
                )
            )
            continue
        # The trade date comes from the fact's own effective period, which
        # `ems.executions` sets to the day of the fill — not from `as_of`,
        # which is when someone happened to ask.
        facts = store.read(subject, _LAST_PX, as_of=as_of)
        trade_date = facts[0].effective_from if facts else as_of.date()

        try:
            series = price_series(store, listing_subject(symbol), as_of=as_of)
        except NoPriceSeriesError as exc:
            unmeasured.append(
                Unmeasured(exec_id=exec_id, symbol=symbol, trade_date=trade_date, reason=str(exc))
            )
            continue

        try:
            measured.append(
                close_benchmark(
                    symbol=symbol,
                    trade_date=trade_date,
                    side=side,
                    executed_price=float(price) if isinstance(price, (int, float)) else 0.0,
                    quantity=float(quantity) if isinstance(quantity, (int, float)) else 0.0,
                    closes=dict(series.points),
                    basis=series.basis,
                ).value
            )
        except BenchmarkUnavailableError as exc:
            unmeasured.append(
                Unmeasured(exec_id=exec_id, symbol=symbol, trade_date=trade_date, reason=str(exc))
            )

    return ExecutionQuality(
        measured=tuple(measured),
        unmeasured=tuple(unmeasured),
        orders=_order_outcomes(store, as_of=as_of),
        unavailable={name: unavailable_reason(name) for name in NOT_COMPUTED},
    )


def _order_outcomes(store: DuckStore, *, as_of: datetime) -> tuple[OrderOutcome, ...]:
    """Join each recorded order to the fills that name it.

    Fills are grouped by their own `orderId` rather than by walking the
    order's subject, because the execution is the record that states the
    link: an order does not know what filled it, and inferring the join
    from anything but the venue's own `ClOrdID` echo would be guessing.

    An order with no fills is still reported, at zero completion. Dropping
    it would hide exactly the orders that did not work.
    """
    fills: dict[str, list[tuple[float, float]]] = {}
    for subject in store.subjects_with_prefix(EXECUTION_PREFIX, as_of=as_of):
        order_id = _value(store, subject, _ORDER_ID, as_of=as_of)
        qty = _value(store, subject, _LAST_QTY, as_of=as_of)
        price = _value(store, subject, _LAST_PX, as_of=as_of)
        if not isinstance(order_id, str) or not isinstance(qty, (int, float)):
            continue
        if not isinstance(price, (int, float)):
            continue
        fills.setdefault(order_id, []).append((float(qty), float(price)))

    outcomes: list[OrderOutcome] = []
    for subject in store.subjects_with_prefix(ORDER_PREFIX, as_of=as_of):
        order_id = str(subject).removeprefix(ORDER_PREFIX)
        symbol = _value(store, subject, _SYMBOL.replace("exec", "order"), as_of=as_of)
        side = _value(store, subject, _SIDE.replace("exec", "order"), as_of=as_of)
        ordered = _value(store, subject, _ORDER_QTY, as_of=as_of)
        limit = _value(store, subject, _ORDER_LIMIT, as_of=as_of)
        if not isinstance(symbol, str) or not isinstance(side, str):
            continue
        if not isinstance(ordered, (int, float)) or not isinstance(limit, (int, float)):
            continue

        matched = fills.get(order_id, [])
        filled = sum(qty for qty, _ in matched)
        # Quantity-weighted, which is what an average fill price means. A
        # mean of the prices would let a one-share print weigh as much as
        # the rest of the order.
        average = sum(qty * price for qty, price in matched) / filled if filled else None
        outcomes.append(
            OrderOutcome(
                order_id=order_id,
                symbol=symbol,
                side=side,
                ordered=float(ordered),
                filled=filled,
                limit_price=float(limit),
                average_price=average,
            )
        )
    return tuple(outcomes)


__all__ = [
    "NOT_COMPUTED",
    "ExecutionQuality",
    "OrderOutcome",
    "Unmeasured",
    "execution_quality",
    "unavailable_reason",
]
