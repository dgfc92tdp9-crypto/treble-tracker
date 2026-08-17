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
EXECUTION_REPORT = "8"

#: ExecType/OrdStatus for a complete fill.
FILLED = "2"


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
    session: Session = field(init=False)
    fills: int = 0

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
            out.append(self._fill(message, now=now))

        return [sent for sent in (self._emit(raw) for raw in out) if sent is not None]

    def _fill(self, order: simplefix.FixMessage, *, now: datetime) -> bytes:
        """One complete fill at the order's own price."""
        self.fills += 1
        quantity = (order.get(ORDER_QTY) or b"0").decode()
        price = (order.get(PRICE) or b"0").decode()
        return self.session.encode(
            EXECUTION_REPORT,
            (
                (CL_ORD_ID, (order.get(CL_ORD_ID) or b"").decode()),
                (EXEC_ID, f"E{self.fills}"),
                (EXEC_TYPE, FILLED),
                (ORD_STATUS, FILLED),
                (SYMBOL, (order.get(SYMBOL) or b"").decode()),
                (SIDE, (order.get(SIDE) or b"").decode()),
                (ORDER_QTY, quantity),
                (LAST_QTY, quantity),
                (LAST_PX, price),
                (CUM_QTY, quantity),
                (LEAVES_QTY, 0),
                (AVG_PX, price),
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


__all__ = [
    "EXECUTION_REPORT",
    "FILLED",
    "NEW_ORDER_SINGLE",
    "Simulator",
    "new_order_single",
]
