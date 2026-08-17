"""An in-repo FIX acceptor to run the session against (P3_3).

The gate criterion asks for connectivity "against a simulator", not against
a venue. A simulator this repository owns is one CI can run with no network
and no external process, which §7 requires — the same reason the ticker
plant ships an `InProcessTransport` beside its NATS and Kafka ones.

**A simulator I wrote, driven by a client I wrote, is a closed loop**, and
that is the risk this file has to answer for. Both halves could share one
misreading of FIX and agree with each other forever. Three things break the
loop:

* `simplefix` encodes and parses both sides — an outside implementation of
  BodyLength and CheckSum. It has already disagreed with a hand-written
  Logon here and been right.
* the tests assert checksums against values computed by hand, byte by byte,
  rather than against whatever this code produces.
* the simulator is *deliberately hostile* where a venue can be: it can be
  told to skip a sequence number, or to reply with a corrupt checksum, so
  the client's refusals are exercised rather than assumed. A simulator that
  only ever behaves well tests the happy path twice.

It fills orders immediately and completely. That is not realistic and is not
pretending to be: partial fills, queue position and rejects are the venue's
behaviour, and inventing a plausible-looking version of them would produce
execution reports nobody should analyse. What this establishes is that the
session and the order path work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import simplefix

from treble.ems.session import (
    MSG_TYPE,
    TEST_REQ_ID,
    MsgType,
    Session,
)

#: Application-level tags the order path uses.
CL_ORD_ID = 11
ORDER_QTY = 38
ORD_TYPE = 40
PRICE = 44
SIDE = 54
SYMBOL = 55
EXEC_ID = 17
EXEC_TYPE = 150
ORD_STATUS = 39
LAST_QTY = 32
LAST_PX = 31
CUM_QTY = 14
LEAVES_QTY = 151
AVG_PX = 6

NEW_ORDER_SINGLE = "D"
CANCEL_REQUEST = "F"
CANCEL_REPLACE_REQUEST = "G"
EXECUTION_REPORT = "8"
CANCEL_REJECT = "9"

ORIG_CL_ORD_ID = 41
CXL_REJ_REASON = 102
CXL_REJ_RESPONSE_TO = 434
TEXT = 58

#: ExecType / OrdStatus values.
NEW = "0"
CANCELED = "4"
REPLACED = "5"

#: ExecType/OrdStatus for a complete fill.
FILLED = "2"


@dataclass
class RestingOrder:
    """An order the simulator is holding rather than filling."""

    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    filled: float = 0.0
    status: str = NEW

    @property
    def live(self) -> bool:
        """Whether the order can still be cancelled or replaced.

        A filled or cancelled order cannot. This is the property the two
        refusals below turn on, and getting it wrong is how a trader comes
        to believe a position was cancelled when it was filled.
        """
        return self.status == NEW


@dataclass
class Simulator:
    """A FIX acceptor that logs on, answers test requests, and fills."""

    sender: str = "SIM"
    target: str = "TREBLE"
    #: Sequence numbers to skip when sending, to manufacture a gap the
    #: client must refuse. A venue drops messages; a simulator that cannot
    #: leaves the client's gap handling untested.
    skip_outbound: frozenset[int] = frozenset()
    #: Corrupt the checksum of these outbound sequence numbers.
    corrupt_outbound: frozenset[int] = frozenset()
    #: When False, orders rest instead of filling, so cancel and replace can
    #: be exercised. A simulator that filled everything instantly would make
    #: every cancel arrive too late and the refusals untestable — true, and
    #: true for the wrong reason.
    fill_immediately: bool = True
    #: Live orders by ClOrdID.
    book: dict[str, RestingOrder] = field(default_factory=dict)
    session: Session = field(init=False)
    fills: int = 0
    reports: int = 0

    def __post_init__(self) -> None:
        self.session = Session(sender=self.sender, target=self.target)

    def _emit(self, raw: bytes) -> bytes | None:
        """Apply the configured hostility to one outbound message."""
        # The number already consumed is the one just used, so read it back
        # rather than the next one — off by one here would corrupt the wrong
        # message and the test would pass for the wrong reason.
        number = self.session.outbound_seq - 1
        if number in self.skip_outbound:
            return None
        if number in self.corrupt_outbound:
            marker = b"\x0110="
            index = raw.rfind(marker)
            stated = int(raw[index + len(marker) :].rstrip(b"\x01"))
            return raw[: index + len(marker)] + f"{(stated + 1) % 256:03d}".encode() + b"\x01"
        return raw

    def respond(self, raw: bytes, *, now: datetime) -> list[bytes]:
        """Handle one inbound message, returning what to send back."""
        message = self.session.receive(raw, require_logon=False)
        msg_type = (message.get(MSG_TYPE) or b"").decode()
        out: list[bytes] = []

        if msg_type == MsgType.LOGON.value:
            out.append(self.session.logon(now=now))
        elif msg_type == MsgType.TEST_REQUEST.value:
            request_id = message.get(TEST_REQ_ID)
            out.append(
                self.session.heartbeat(
                    now=now, test_req_id=request_id.decode() if request_id else None
                )
            )
        elif msg_type == MsgType.RESEND_REQUEST.value:
            # Replay from the store the session keeps. A resend that
            # re-encoded the message would give it a new sending time and a
            # new checksum, which is a different message wearing the same
            # sequence number.
            begin = int(message.get(7) or 1)
            end = int(message.get(16) or 0) or max(self.session.sent, default=0)
            out.extend(
                self.session.sent[n] for n in range(begin, end + 1) if n in self.session.sent
            )
            return out
        elif msg_type == NEW_ORDER_SINGLE:
            out.append(self._accept(message, now=now))
        elif msg_type == CANCEL_REQUEST:
            out.append(self._cancel(message, now=now))
        elif msg_type == CANCEL_REPLACE_REQUEST:
            out.append(self._replace(message, now=now))

        return [sent for sent in (self._emit(raw) for raw in out) if sent is not None]

    def _accept(self, order: simplefix.FixMessage, *, now: datetime) -> bytes:
        """Fill the order, or rest it, depending on the configured mode."""
        order_id = (order.get(CL_ORD_ID) or b"").decode()
        if order_id in self.book:
            # A ClOrdID must be unique per session. Reusing one makes every
            # later cancel ambiguous, and a venue that accepted it would
            # leave two orders answering to one name.
            return self._cancel_reject(
                order_id, order_id, "duplicate ClOrdID", response_to=NEW_ORDER_SINGLE, now=now
            )
        resting = RestingOrder(
            order_id=order_id,
            symbol=(order.get(SYMBOL) or b"").decode(),
            side=(order.get(SIDE) or b"").decode(),
            quantity=float((order.get(ORDER_QTY) or b"0").decode()),
            price=float((order.get(PRICE) or b"0").decode()),
        )
        self.book[order_id] = resting
        if not self.fill_immediately:
            return self._report(resting, NEW, now=now, last_qty=0.0)
        self.fills += 1
        resting.filled = resting.quantity
        resting.status = FILLED
        return self._report(resting, FILLED, now=now, last_qty=resting.quantity)

    def _cancel(self, request: simplefix.FixMessage, *, now: datetime) -> bytes:
        """Cancel a resting order, or refuse and say why.

        **The refusal is the point.** A cancel that arrives after the fill
        must be rejected, never acknowledged: a trader who believes an order
        was cancelled when it was filled is long or short something they
        think they are flat, and every downstream number agrees with them.
        """
        order_id = (request.get(CL_ORD_ID) or b"").decode()
        original = (request.get(ORIG_CL_ORD_ID) or b"").decode()
        resting = self.book.get(original)
        if resting is None:
            return self._cancel_reject(
                order_id, original, "unknown order", response_to=CANCEL_REQUEST, now=now
            )
        if not resting.live:
            return self._cancel_reject(
                order_id,
                original,
                f"too late to cancel: already {resting.status}",
                response_to=CANCEL_REQUEST,
                now=now,
            )
        resting.status = CANCELED
        return self._report(resting, CANCELED, now=now, last_qty=0.0, order_id=order_id)

    def _replace(self, request: simplefix.FixMessage, *, now: datetime) -> bytes:
        """Amend a resting order's quantity or price, or refuse."""
        order_id = (request.get(CL_ORD_ID) or b"").decode()
        original = (request.get(ORIG_CL_ORD_ID) or b"").decode()
        resting = self.book.get(original)
        if resting is None:
            return self._cancel_reject(
                order_id, original, "unknown order", response_to=CANCEL_REPLACE_REQUEST, now=now
            )
        if not resting.live:
            return self._cancel_reject(
                order_id,
                original,
                f"too late to replace: already {resting.status}",
                response_to=CANCEL_REPLACE_REQUEST,
                now=now,
            )
        new_quantity = float((request.get(ORDER_QTY) or b"0").decode())
        if new_quantity < resting.filled:
            # Reducing below what is already done is not an amendment, it is
            # an instruction to un-execute. Refused rather than clamped: a
            # silent clamp would leave the trader believing a smaller
            # position than they hold.
            return self._cancel_reject(
                order_id,
                original,
                f"quantity {new_quantity:g} is below {resting.filled:g} already filled",
                response_to=CANCEL_REPLACE_REQUEST,
                now=now,
            )
        replaced = RestingOrder(
            order_id=order_id,
            symbol=resting.symbol,
            side=resting.side,
            quantity=new_quantity,
            price=float((request.get(PRICE) or b"0").decode()) or resting.price,
            filled=resting.filled,
        )
        # The original leaves the book under its own id and the replacement
        # enters under the new one. Keeping both live would make a later
        # cancel ambiguous, which is the defect the duplicate check above
        # exists to prevent.
        resting.status = REPLACED
        self.book[order_id] = replaced
        return self._report(replaced, REPLACED, now=now, last_qty=0.0, original=original)

    def _report(
        self,
        order: RestingOrder,
        exec_type: str,
        *,
        now: datetime,
        last_qty: float,
        order_id: str | None = None,
        original: str | None = None,
    ) -> bytes:
        self.reports += 1
        fields: list[tuple[int, str | int]] = [
            (CL_ORD_ID, order_id or order.order_id),
            (EXEC_ID, f"E{self.reports}"),
            (EXEC_TYPE, exec_type),
            (ORD_STATUS, exec_type),
            (SYMBOL, order.symbol),
            (SIDE, order.side),
            (ORDER_QTY, _decimal(order.quantity)),
            (LAST_QTY, _decimal(last_qty)),
            (LAST_PX, _decimal(order.price if last_qty else 0.0)),
            (CUM_QTY, _decimal(order.filled)),
            (LEAVES_QTY, _decimal(max(order.quantity - order.filled, 0.0) if order.live else 0.0)),
            (AVG_PX, _decimal(order.price if order.filled else 0.0)),
        ]
        if original is not None:
            fields.insert(1, (ORIG_CL_ORD_ID, original))
        return self.session.encode(EXECUTION_REPORT, tuple(fields), now=now)

    def _cancel_reject(
        self, order_id: str, original: str, reason: str, *, response_to: str, now: datetime
    ) -> bytes:
        """Say no, and say why.

        A rejection carrying no reason is one a trader cannot act on: they
        do not know whether to retry, to re-send with a different id, or to
        check their position because the order already filled.
        """
        return self.session.encode(
            CANCEL_REJECT,
            (
                (CL_ORD_ID, order_id),
                (ORIG_CL_ORD_ID, original),
                (ORD_STATUS, self.book[original].status if original in self.book else "8"),
                (CXL_REJ_RESPONSE_TO, "1" if response_to == CANCEL_REQUEST else "2"),
                (CXL_REJ_REASON, "0"),
                (TEXT, reason),
            ),
            now=now,
        )


def _decimal(value: float) -> str:
    """Plain decimal, never scientific notation.

    `f"{1_000_000:g}"` is `1e+06`, which is not a FIX quantity. A venue
    either rejects it or parses it as something else, and a one-million
    order that arrives as anything but one million is the worst available
    outcome on this path. Caught by reading a fill off the simulator and
    seeing `32=1e+06` on the wire.

    Trailing zeros are trimmed because FIX quantities are decimals, not
    fixed-point, and `1000000.0` is wider than it needs to be.
    """
    text = f"{value:.8f}".rstrip("0").rstrip(".")
    return text or "0"


def new_order_single(
    session: Session,
    *,
    order_id: str,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    now: datetime,
) -> bytes:
    """A limit order.

    Limit only. A market order carries no price, and sending one into a
    simulator that fills at the order's price would fill at zero — a number
    that would propagate into every position and P&L downstream looking like
    a real execution.
    """
    return session.encode(
        NEW_ORDER_SINGLE,
        (
            (CL_ORD_ID, order_id),
            (SYMBOL, symbol),
            (SIDE, side),
            (ORDER_QTY, _decimal(quantity)),
            (ORD_TYPE, "2"),
            (PRICE, _decimal(price)),
        ),
        now=now,
    )


def cancel_request(
    session: Session, *, order_id: str, original_id: str, symbol: str, side: str, now: datetime
) -> bytes:
    """Ask to cancel a resting order."""
    return session.encode(
        CANCEL_REQUEST,
        (
            (CL_ORD_ID, order_id),
            (ORIG_CL_ORD_ID, original_id),
            (SYMBOL, symbol),
            (SIDE, side),
        ),
        now=now,
    )


def cancel_replace_request(
    session: Session,
    *,
    order_id: str,
    original_id: str,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    now: datetime,
) -> bytes:
    """Ask to amend a resting order's quantity or price."""
    return session.encode(
        CANCEL_REPLACE_REQUEST,
        (
            (CL_ORD_ID, order_id),
            (ORIG_CL_ORD_ID, original_id),
            (SYMBOL, symbol),
            (SIDE, side),
            (ORDER_QTY, _decimal(quantity)),
            (ORD_TYPE, "2"),
            (PRICE, _decimal(price)),
        ),
        now=now,
    )


__all__ = [
    "CANCELED",
    "CANCEL_REJECT",
    "CANCEL_REPLACE_REQUEST",
    "CANCEL_REQUEST",
    "EXECUTION_REPORT",
    "FILLED",
    "NEW",
    "NEW_ORDER_SINGLE",
    "REPLACED",
    "RestingOrder",
    "Simulator",
    "cancel_replace_request",
    "cancel_request",
    "new_order_single",
]
