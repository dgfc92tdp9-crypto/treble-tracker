"""The FIX session layer: sequence, heartbeat, and what must be refused.

FIX splits into a *session* protocol and an *application* protocol, and
almost every way of losing money here is in the session half. An order that
is rejected is visible. A message that was never delivered is not.

**The sequence number is the whole mechanism, and a gap must never be
absorbed.** FIX guarantees ordered, gapless delivery per session by counting
every message; a counterparty that sends 5 after 3 is telling you that 4
exists and you do not have it. If message 4 was an execution report you now
hold a fill you do not know about, and every position, P&L and risk number
downstream is wrong while looking entirely normal. So a gap raises a
`SequenceGapError` naming both numbers and demands a resend — it is never
logged and stepped over.

That is the same rule as the ticker plant's `trade_id` sequencing and the
store's refusal to write a fact with a dangling provenance id: **the system
declines to hold data it cannot account for.**

**Messages are encoded and parsed by `simplefix`, deliberately.** It is an
outside implementation of BodyLength and CheckSum, and using it means the
session layer is checked against someone else's reading of the protocol
rather than against its own. That mattered immediately: it disagreed with a
Logon this author hand-wrote, and it was right — the body length was 68 and
not 65, confirmed by counting the bytes a third time. A session built on a
home-made encoder and tested against that same encoder would have agreed
with itself forever.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import UTC, datetime

import simplefix

#: FIX field tags this layer reads. Named rather than inlined: `34` and `35`
#: are indistinguishable at a glance and swapping them parses cleanly.
BEGIN_STRING = 8
BODY_LENGTH = 9
MSG_TYPE = 35
SEQ_NUM = 34
SENDER = 49
SENDING_TIME = 52
TARGET = 56
ENCRYPT_METHOD = 98
HEARTBEAT_INTERVAL = 108
TEST_REQ_ID = 112
BEGIN_SEQ_NO = 7
END_SEQ_NO = 16
GAP_FILL_FLAG = 123
NEW_SEQ_NO = 36
CHECKSUM = 10

#: The version this session speaks. 4.4 rather than 4.2 because it is the
#: last widely-deployed version before FIXT split session from application,
#: and the one simulators and venues most consistently accept.
BEGIN_STRING_VALUE = "FIX.4.4"

#: Seconds. Both sides agree it at Logon; 30 is the conventional default.
DEFAULT_HEARTBEAT = 30


class MsgType(enum.Enum):
    """Session-level message types. Application types live with the order."""

    HEARTBEAT = "0"
    TEST_REQUEST = "1"
    RESEND_REQUEST = "2"
    REJECT = "3"
    SEQUENCE_RESET = "4"
    LOGOUT = "5"
    LOGON = "A"


class SessionError(RuntimeError):
    """The session cannot continue as it stands."""


class NotLoggedOnError(SessionError):
    """An application message arrived before Logon.

    Refused rather than processed. FIX requires Logon first, and a venue
    that sends an execution report before one is either misconfigured or not
    the venue you think it is — accepting the fill would be trusting an
    unauthenticated peer with a position.
    """


class SequenceGapError(SessionError):
    """A message arrived with a sequence number ahead of the expected one.

    Carries both numbers because the difference is what must be resent. A
    gap is never absorbed: the missing messages may be fills, and a position
    built from an incomplete stream is wrong while looking normal.
    """

    def __init__(self, expected: int, received: int) -> None:
        self.expected = expected
        self.received = received
        super().__init__(
            f"sequence gap: expected {expected}, received {received}. "
            f"{received - expected} message(s) missing — request a resend rather than "
            "continuing, because a missing execution report is a fill you do not know "
            "you hold"
        )


class ChecksumError(SessionError):
    """The trailing checksum does not match the bytes before it."""


@dataclass
class Session:
    """One FIX session's state, from either side of the wire.

    Symmetrical on purpose: an initiator and an acceptor differ in who sends
    Logon first, not in how sequence numbers work. Writing two of these
    would be writing two chances to get the counting wrong.
    """

    sender: str
    target: str
    heartbeat_seconds: int = DEFAULT_HEARTBEAT
    #: Next number this side will *send*. FIX starts both sides at 1.
    outbound_seq: int = 1
    #: Next number this side *expects* to receive.
    inbound_seq: int = 1
    logged_on: bool = False
    #: Every message sent, by sequence number, so a resend can be answered.
    #: A session that cannot resend is one whose counterparty's gap is
    #: unrecoverable — and the gap is usually theirs, not yours.
    sent: dict[int, bytes] = field(default_factory=dict)

    # -- outbound -------------------------------------------------------

    def encode(
        self,
        msg_type: MsgType | str,
        fields: tuple[tuple[int, str | int], ...] = (),
        *,
        now: datetime,
    ) -> bytes:
        """Build one message and advance the outbound counter.

        The counter advances here rather than at the transport, because a
        message that was built and not sent still consumed its number as far
        as this side is concerned — and a reused number is a worse failure
        than a gap, since the counterparty silently discards the second.
        """
        message = simplefix.FixMessage()
        message.append_pair(BEGIN_STRING, BEGIN_STRING_VALUE)
        # `MsgType` for session messages, a bare string for application
        # ones. One encoder rather than two: the header, the counter and the
        # resend store are identical either way, and a second copy would be
        # a second place for the sequence number to go wrong.
        message.append_pair(MSG_TYPE, msg_type.value if isinstance(msg_type, MsgType) else msg_type)
        message.append_pair(SEQ_NUM, self.outbound_seq)
        message.append_pair(SENDER, self.sender)
        message.append_pair(TARGET, self.target)
        message.append_pair(SENDING_TIME, now.astimezone(UTC).strftime("%Y%m%d-%H:%M:%S.%f")[:-3])
        for tag, value in fields:
            message.append_pair(tag, value)
        raw = bytes(message.encode())
        self.sent[self.outbound_seq] = raw
        self.outbound_seq += 1
        return raw

    def logon(self, *, now: datetime) -> bytes:
        return self.encode(
            MsgType.LOGON,
            ((ENCRYPT_METHOD, 0), (HEARTBEAT_INTERVAL, self.heartbeat_seconds)),
            now=now,
        )

    def heartbeat(self, *, now: datetime, test_req_id: str | None = None) -> bytes:
        """A heartbeat, echoing a TestRequest id when answering one.

        The echo is not decoration: a TestRequest asks "are you there *now*",
        and a heartbeat without the id could be a scheduled one that crossed
        in flight. Without it a live session and a dead one look alike for
        one interval.
        """
        extra = ((TEST_REQ_ID, test_req_id),) if test_req_id else ()
        return self.encode(MsgType.HEARTBEAT, extra, now=now)

    def resend_request(self, *, begin: int, end: int, now: datetime) -> bytes:
        """Ask for a range again. `end` of 0 means 'through to the latest'."""
        return self.encode(
            MsgType.RESEND_REQUEST, ((BEGIN_SEQ_NO, begin), (END_SEQ_NO, end)), now=now
        )

    # -- inbound --------------------------------------------------------

    def receive(self, raw: bytes, *, require_logon: bool = True) -> simplefix.FixMessage:
        """Validate and accept one inbound message, advancing the counter.

        Order matters and is not arbitrary: checksum, then sequence, then
        logon state. A message whose bytes are corrupt has no trustworthy
        sequence number to complain about, so checking sequence first would
        be reasoning from a field that may be noise.
        """
        verify_checksum(raw)
        parser = simplefix.FixParser()
        parser.append_buffer(raw)
        message = parser.get_message()
        if message is None:
            raise ChecksumError("incomplete message: no trailing checksum field")

        received = int(message.get(SEQ_NUM) or 0)
        msg_type = (message.get(MSG_TYPE) or b"").decode()

        if received > self.inbound_seq:
            # Raised *before* the counter moves, so a caller that catches it
            # and requests a resend has the session still pointing at the
            # first missing message.
            raise SequenceGapError(self.inbound_seq, received)
        if received < self.inbound_seq:
            # A duplicate, which FIX permits during resend. Accepted without
            # advancing: the alternative is rejecting a legitimate resend.
            return message

        if msg_type == MsgType.LOGON.value:
            self.logged_on = True
            negotiated = message.get(HEARTBEAT_INTERVAL)
            if negotiated is not None:
                self.heartbeat_seconds = int(negotiated)
        elif require_logon and not self.logged_on:
            raise NotLoggedOnError(
                f"message type {msg_type!r} arrived before Logon. FIX requires Logon "
                "first, and a peer sending business messages without one is either "
                "misconfigured or not the peer you think it is"
            )

        self.inbound_seq += 1
        return message


def verify_checksum(raw: bytes) -> None:
    """Check the trailing CheckSum against the bytes it covers.

    Computed here rather than trusted to the parser, because the parser's
    job is to read fields and this is the one field that says whether the
    fields can be believed. A corrupted tag inside an otherwise well-formed
    message parses cleanly and means something else.
    """
    marker = b"\x0110="
    index = raw.rfind(marker)
    if index == -1:
        raise ChecksumError("no CheckSum field")
    covered = raw[: index + 1]
    stated = raw[index + len(marker) :].rstrip(b"\x01")
    expected = f"{sum(covered) % 256:03d}".encode()
    if stated != expected:
        raise ChecksumError(
            f"checksum {stated.decode()} does not match {expected.decode()} computed over "
            f"{len(covered)} bytes. The message is corrupt, so nothing in it — including "
            "its sequence number — can be relied on"
        )


__all__ = [
    "BEGIN_STRING_VALUE",
    "DEFAULT_HEARTBEAT",
    "ChecksumError",
    "MsgType",
    "NotLoggedOnError",
    "SequenceGapError",
    "Session",
    "SessionError",
    "verify_checksum",
]
