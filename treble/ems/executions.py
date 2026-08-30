"""Executions, as facts, from the FIX messages that reported them (P3_5).

TCA needs executions to analyse and this install had none. `ems/store.py`
persists FIX sequence numbers, `tapi/positions.py` reads *fund* holdings out
of N-PORT — neither is a record of what this workstation traded. So the
ledger's note on P3_5 said it twice: blocked on a benchmark price, and
blocked on a trade store, "the half that is buildable". This is that half.

## Why the fact store rather than a table of its own

`people/directory.py` is deliberately its own store, because personal data
cannot be append-only and still honour an erasure request. Executions are
the opposite case: a record of what happened, which must never change, and
whose value is precisely its history. That is what I2 is for, so they go in
`facts` and inherit bitemporality, point-in-time reads and the visibility
window without a second mechanism to keep in step.

## Provenance is a message that actually exists

I1 wants every field to say where it came from, and the honest answer here
is not "the EMS said so". It is the execution report itself — which
`ems/transport.py` already archives into the content-addressed vault before
anything is derived from it, exactly as `SourceAdapter.run` stores a payload
before parsing it.

So `payload_hash` on an execution's provenance is the vault key of the FIX
message, and the bytes behind a fill can be fetched back and re-parsed. That
is a real chain rather than a plausible-looking one: without the archive
this module would have had to invent a hash or leave the field empty, and
both are worse than not making the claim.

`parse_execution` is a pure function of the message for the same reason
adapter `parse` methods are — it is what lets the vault be replayed into a
store, and what the recorder below depends on rather than duplicating.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import simplefix

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance

#: MsgType for an execution report.
EXECUTION_REPORT = b"8"

#: The tags read here, named rather than inlined so a wrong number is a
#: wrong constant rather than a wrong digit in the middle of an expression.
MSG_TYPE = 35
CL_ORD_ID = 11
EXEC_ID = 17
EXEC_TYPE = 150
ORD_STATUS = 39
SYMBOL = 55
SIDE = 54
LAST_QTY = 32
LAST_PX = 31
CUM_QTY = 14
AVG_PX = 6
ORDER_QTY = 38
TRANSACT_TIME = 60

#: `ExecType` values that report a trade. An execution report is *not*
#: necessarily an execution: an acknowledgement, a cancel and a reject all
#: arrive as 35=8. Counting those as fills is how a TCA report ends up
#: averaging a price of zero into its own benchmark.
#:
#: `F` is FIX 4.3+ for "trade"; `1` and `2` are 4.2's partial-fill and fill.
#: All three are accepted because the session layer speaks 4.2 and 4.4, and
#: a version-dependent silence here would drop every fill from one of them.
TRADE_EXEC_TYPES = frozenset({b"F", b"1", b"2"})

#: FIX side codes this maps. Anything else is refused rather than guessed:
#: a short sale booked as a buy is a position error with a plausible
#: explanation, which is the kind that survives review.
SIDES: dict[bytes, str] = {b"1": "buy", b"2": "sell", b"5": "sell_short", b"6": "sell_short_exempt"}

#: Subject prefix. One subject per execution, keyed by the venue's own
#: `ExecID`, so re-recording the same report is the same subject and the
#: store's latest-knowledge-wins window collapses it rather than double
#: counting a fill.
EXECUTION_PREFIX = "exec:"


class NotAnExecutionError(ValueError):
    """The message is not an execution report, or reports no trade.

    Raised rather than returning None so a caller cannot skip the check by
    accident. `record_execution` catches it; a caller that wants the
    distinction can ask for it.
    """


def execution_subject(exec_id: str) -> TUID:
    return TUID(f"{EXECUTION_PREFIX}{exec_id}")


@dataclass(frozen=True)
class Execution:
    """One fill, as the venue reported it.

    Frozen because this is a record of something that happened. The fields
    are the venue's numbers, not derived ones: `average_price` is what the
    report said, not a recomputation, so a disagreement between the venue's
    running average and ours is visible rather than overwritten.
    """

    exec_id: str
    order_id: str
    symbol: str
    side: str
    last_qty: float
    last_px: float
    cum_qty: float
    average_price: float
    order_qty: float
    transact_time: datetime

    def __post_init__(self) -> None:
        if self.last_qty <= 0:
            raise ValueError(
                f"{self.exec_id}: a trade with quantity {self.last_qty} is not a trade"
            )
        if self.last_px < 0:
            raise ValueError(f"{self.exec_id}: negative price {self.last_px}")
        if self.transact_time.tzinfo is None:
            raise ValueError(f"{self.exec_id}: transact_time must be timezone-aware")

    @property
    def notional(self) -> float:
        return self.last_qty * self.last_px

    @property
    def signed_qty(self) -> float:
        """Quantity with the sign of the side, for position arithmetic.

        A sell is negative. Kept as a property rather than stored so the
        side and the sign cannot disagree.
        """
        return -self.last_qty if self.side.startswith("sell") else self.last_qty


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


def _transact_time(message: simplefix.FixMessage) -> datetime | None:
    """`TransactTime` as an aware UTC datetime.

    FIX stamps are `YYYYMMDD-HH:MM:SS` with optional fractional seconds and
    no zone — UTC by the specification, so the zone is attached rather than
    assumed away. A naive datetime here would make every point-in-time read
    of an execution depend on the reader's locale.
    """
    raw = _text(message, TRANSACT_TIME)
    if raw is None:
        return None
    for shape in ("%Y%m%d-%H:%M:%S.%f", "%Y%m%d-%H:%M:%S"):
        try:
            return datetime.strptime(raw, shape).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def parse_execution(raw: bytes, *, received_at: datetime | None = None) -> Execution:
    """One execution report's bytes -> an `Execution`. Pure.

    ``received_at`` is the fallback for a report carrying no `TransactTime`.
    Passed in rather than read from the clock, because a parse that reads
    the wall clock cannot be replayed — the defect the nightly replay round
    trip exists to catch, and there is no reason to introduce it here.
    """
    message = simplefix.FixParser()
    message.append_buffer(raw)
    parsed = message.get_message()
    if parsed is None:
        raise NotAnExecutionError("not a complete FIX message")
    if parsed.get(MSG_TYPE) != EXECUTION_REPORT:
        raise NotAnExecutionError(f"MsgType {_text(parsed, MSG_TYPE)!r} is not an execution report")

    exec_type = parsed.get(EXEC_TYPE)
    if exec_type not in TRADE_EXEC_TYPES:
        # An ack, a cancel or a reject. Not an error — most execution
        # reports are not trades — but not a fill either.
        raise NotAnExecutionError(f"ExecType {exec_type!r} reports no trade")

    exec_id = _text(parsed, EXEC_ID)
    order_id = _text(parsed, CL_ORD_ID)
    symbol = _text(parsed, SYMBOL)
    side_code = parsed.get(SIDE)
    last_qty = _number(parsed, LAST_QTY)
    last_px = _number(parsed, LAST_PX)
    if exec_id is None or order_id is None or symbol is None:
        raise NotAnExecutionError("execution report is missing ExecID, ClOrdID or Symbol")
    if side_code not in SIDES:
        raise NotAnExecutionError(f"unmapped Side {side_code!r}")
    if last_qty is None or last_px is None:
        raise NotAnExecutionError("execution report carries no LastQty/LastPx")

    when = _transact_time(parsed) or received_at
    if when is None:
        raise NotAnExecutionError("no TransactTime and no received_at to fall back to")

    return Execution(
        exec_id=exec_id,
        order_id=order_id,
        symbol=symbol,
        side=SIDES[side_code],
        last_qty=last_qty,
        last_px=last_px,
        cum_qty=_number(parsed, CUM_QTY) or last_qty,
        average_price=_number(parsed, AVG_PX) or last_px,
        order_qty=_number(parsed, ORDER_QTY) or 0.0,
        transact_time=when,
    )


def execution_provenance(
    payload_hash: str, *, received_at: datetime, source_uri: str
) -> Provenance:
    """Provenance for a fill, pointing at the archived FIX message.

    ``payload_hash`` is the vault key of the raw report. Required, not
    optional: an execution fact whose provenance names no payload is a
    number with a story attached, and the whole point of archiving the
    message first is that the story is checkable.
    """
    if not payload_hash:
        raise ValueError("an execution's provenance needs the archived message's key")
    return Provenance(
        source_system="ems",
        source_uri=source_uri,
        retrieved_at=received_at,
        # `FEED`, not a new `FIX` member. A FIX session is a streaming
        # dissemination feed, which is what this enum already means, and
        # adding a member would change nothing about the claim while giving
        # every existing `match` on this enum a case it has never seen.
        method=ExtractionMethod.FEED,
        extractor_version=PARSER_VERSION,
        payload_hash=payload_hash,
    )


#: Bumped when this parser's output changes for the same bytes, so a replay
#: can tell a corrected reading from a new event — the same contract every
#: ingest adapter's `parser_version` carries.
PARSER_VERSION = "1"

#: The fields written per execution. Stated as a tuple so the set is one
#: thing to read, and so a field added without a reader is visible to the
#: unread-members gate rather than accumulating quietly.
EXECUTION_FIELDS = (
    "ems:exec:orderId",
    "ems:exec:symbol",
    "ems:exec:side",
    "ems:exec:lastQty",
    "ems:exec:lastPx",
    "ems:exec:cumQty",
    "ems:exec:avgPx",
    "ems:exec:orderQty",
)


def execution_facts(execution: Execution, provenance_id: str) -> tuple[Fact, ...]:
    """An execution as facts, all keyed to the same subject.

    `effective_from` and `effective_to` are both the trade date: a fill
    happened at an instant and is not true "from then on", which is what an
    open-ended period would claim. `knowledge_from` is the same instant,
    because for our own execution there is no gap between the event and
    learning of it — unlike a filing, where the two genuinely differ.
    """
    subject = execution_subject(execution.exec_id)
    day = execution.transact_time.date()
    values: dict[str, str | float] = {
        "ems:exec:orderId": execution.order_id,
        "ems:exec:symbol": execution.symbol,
        "ems:exec:side": execution.side,
        "ems:exec:lastQty": execution.last_qty,
        "ems:exec:lastPx": execution.last_px,
        "ems:exec:cumQty": execution.cum_qty,
        "ems:exec:avgPx": execution.average_price,
        "ems:exec:orderQty": execution.order_qty,
    }
    return tuple(
        Fact(
            subject=subject,
            field=field,
            value=values[field],
            effective_from=day,
            effective_to=day,
            knowledge_from=execution.transact_time,
            provenance_id=provenance_id,
        )
        for field in EXECUTION_FIELDS
    )


__all__ = [
    "EXECUTION_FIELDS",
    "EXECUTION_PREFIX",
    "EXECUTION_REPORT",
    "PARSER_VERSION",
    "SIDES",
    "TRADE_EXEC_TYPES",
    "Execution",
    "NotAnExecutionError",
    "execution_facts",
    "execution_provenance",
    "execution_subject",
    "parse_execution",
]
