"""Orders, as facts, from the messages that placed them (P3_5).

The execution store recorded what was *filled*. This records what was
*asked for*, and the difference is where transaction cost analysis actually
lives: a fill on its own can only be compared to the market, while an order
can be compared to the decision that produced it.

Concretely, it is what the P3_5 ledger entry called the nearer blocker.
Arrival-price benchmarks need a price at the instant the order arrived, and
this install had no record of that instant at all: an execution carries its
`ClOrdID`, and nothing stored the order it named.

## What this does and does not unblock

It supplies the *time*. It does not supply the *price* at that time, and
`analytics.tca` still refuses arrival for that reason: this store holds one
close per day, and scoring a 14:30 order against a 21:00 close and calling
it arrival price would give a number the right name and the wrong meaning.

What it does unblock now is the order-to-fill join — completion, and each
fill measured against the limit the trader actually set, which is a
statement about execution rather than about the market.

## TransactTime is the arrival time

`ems.executions` found the simulator omitting tag 60 from execution
reports. `new_order_single` omitted it too, and here it matters more: on an
execution report tag 60 is when the trade happened, and on an order it is
when the trader decided. Without it an order's arrival is whenever the
acceptor happened to read the socket, which is a property of network
scheduling rather than of the decision.

`SendingTime` (52) is the fallback before `received_at`, because the
session layer stamps it on every message and it is the client's own clock
at the moment it sent — closer to the decision than the acceptor's read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import simplefix

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance

#: Shared with `ems.executions` rather than re-listed. A side mapped one way
#: on the order and another on the fill would make the join silently wrong,
#: and two lists of FIX codes is exactly how that happens.
from treble.ems.executions import SIDES

#: MsgType for a new order.
NEW_ORDER_SINGLE = b"D"

MSG_TYPE = 35
CL_ORD_ID = 11
SYMBOL = 55
SIDE = 54
ORDER_QTY = 38
ORD_TYPE = 40
PRICE = 44
SENDING_TIME = 52
TRANSACT_TIME = 60

#: FIX order types this maps. Market (`1`) is deliberately absent: a market
#: order carries no price, and the simulator refuses to send one for the
#: reason its own docstring gives — filling at the order's price would fill
#: at zero. Recording one here would put a limit of 0.0 in the book.
ORDER_TYPES: dict[bytes, str] = {b"2": "limit"}

#: Subject prefix, keyed by `ClOrdID` — which is what an execution report
#: carries, so the join is on the identifier the venue itself echoes back.
ORDER_PREFIX = "order:"

PARSER_VERSION = "1"

#: Fields written per order. `ems:order:arrival` is stored as text rather
#: than a date because it is an *instant*: the whole point is the time of
#: day, and a date-typed value would round the decision to midnight.
ORDER_FIELDS = (
    "ems:order:symbol",
    "ems:order:side",
    "ems:order:quantity",
    "ems:order:type",
    "ems:order:limitPrice",
    "ems:order:arrival",
)


class NotAnOrderError(ValueError):
    """The message is not a new order this module can record."""


def order_subject(order_id: str) -> TUID:
    return TUID(f"{ORDER_PREFIX}{order_id}")


@dataclass(frozen=True)
class Order:
    """One order, as it was placed."""

    order_id: str
    symbol: str
    side: str
    quantity: float
    order_type: str
    limit_price: float
    arrival_time: datetime

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"{self.order_id}: order quantity {self.quantity} is not positive")
        if self.limit_price <= 0:
            raise ValueError(
                f"{self.order_id}: limit price {self.limit_price} is not positive. A limit of "
                "zero would score every fill against it as infinitely bad."
            )
        if self.arrival_time.tzinfo is None:
            raise ValueError(f"{self.order_id}: arrival_time must be timezone-aware")


def _text(message: simplefix.FixMessage, tag: int) -> str | None:
    value = message.get(tag)
    return None if value is None else value.decode()


def _number(message: simplefix.FixMessage, tag: int) -> float | None:
    raw = _text(message, tag)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _stamp(message: simplefix.FixMessage, tag: int) -> datetime | None:
    raw = _text(message, tag)
    if raw is None:
        return None
    for shape in ("%Y%m%d-%H:%M:%S.%f", "%Y%m%d-%H:%M:%S"):
        try:
            return datetime.strptime(raw, shape).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def parse_order(raw: bytes, *, received_at: datetime | None = None) -> Order:
    """One NewOrderSingle's bytes -> an `Order`. Pure.

    The arrival time is `TransactTime`, then `SendingTime`, then
    ``received_at`` — decision, transmission, receipt, in that order of
    preference, because that is their order of closeness to the moment the
    trader chose. None of the three is the wall clock: a parse that read it
    could not be replayed.
    """
    parser = simplefix.FixParser()
    parser.append_buffer(raw)
    parsed = parser.get_message()
    if parsed is None:
        raise NotAnOrderError("not a complete FIX message")
    if parsed.get(MSG_TYPE) != NEW_ORDER_SINGLE:
        raise NotAnOrderError(f"MsgType {_text(parsed, MSG_TYPE)!r} is not a new order")

    order_id = _text(parsed, CL_ORD_ID)
    symbol = _text(parsed, SYMBOL)
    side_code = parsed.get(SIDE)
    order_type_code = parsed.get(ORD_TYPE)
    quantity = _number(parsed, ORDER_QTY)
    price = _number(parsed, PRICE)

    if order_id is None or symbol is None:
        raise NotAnOrderError("new order is missing ClOrdID or Symbol")
    if side_code not in SIDES:
        raise NotAnOrderError(f"unmapped Side {side_code!r}")
    if order_type_code not in ORDER_TYPES:
        raise NotAnOrderError(
            f"unmapped OrdType {order_type_code!r}; only {sorted(ORDER_TYPES)} are recorded"
        )
    if quantity is None or price is None:
        raise NotAnOrderError("new order carries no OrderQty/Price")

    arrival = _stamp(parsed, TRANSACT_TIME) or _stamp(parsed, SENDING_TIME) or received_at
    if arrival is None:
        raise NotAnOrderError("no TransactTime, SendingTime or received_at to date this order")

    return Order(
        order_id=order_id,
        symbol=symbol,
        side=SIDES[side_code],
        quantity=quantity,
        order_type=ORDER_TYPES[order_type_code],
        limit_price=price,
        arrival_time=arrival,
    )


def order_provenance(payload_hash: str, *, received_at: datetime, source_uri: str) -> Provenance:
    """Provenance for an order, pointing at the archived message.

    Same contract as an execution's: required, because a fact whose
    provenance names no payload cannot be checked, and the archive exists
    so that it can be.
    """
    if not payload_hash:
        raise ValueError("an order's provenance needs the archived message's key")
    return Provenance(
        source_system="ems",
        source_uri=source_uri,
        retrieved_at=received_at,
        method=ExtractionMethod.FEED,
        extractor_version=PARSER_VERSION,
        payload_hash=payload_hash,
    )


def order_facts(order: Order, provenance_id: str) -> tuple[Fact, ...]:
    """An order as facts, keyed to one subject.

    Effective on the arrival day and closed there, like an execution: an
    order was placed at an instant. `knowledge_from` is the arrival time,
    because the workstation that placed it knew immediately.
    """
    subject = order_subject(order.order_id)
    day = order.arrival_time.date()
    values: dict[str, str | float] = {
        "ems:order:symbol": order.symbol,
        "ems:order:side": order.side,
        "ems:order:quantity": order.quantity,
        "ems:order:type": order.order_type,
        "ems:order:limitPrice": order.limit_price,
        "ems:order:arrival": order.arrival_time.isoformat(),
    }
    return tuple(
        Fact(
            subject=subject,
            field=field,
            value=values[field],
            effective_from=day,
            effective_to=day,
            knowledge_from=order.arrival_time,
            provenance_id=provenance_id,
        )
        for field in ORDER_FIELDS
    )


__all__ = [
    "NEW_ORDER_SINGLE",
    "ORDER_FIELDS",
    "ORDER_PREFIX",
    "ORDER_TYPES",
    "PARSER_VERSION",
    "NotAnOrderError",
    "Order",
    "order_facts",
    "order_provenance",
    "order_subject",
    "parse_order",
]
