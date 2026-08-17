"""The FIX session layer (P3_3).

FIX splits into a session protocol and an application protocol, and almost
every way of losing money here is in the session half. An order that is
rejected is visible; a message that was never delivered is not.

**The sequence-gap tests are the point of this file.** A counterparty that
sends 5 after 3 is telling you 4 exists and you do not have it, and if 4 was
an execution report you hold a fill you do not know about — every position,
P&L and risk figure downstream is then wrong while looking entirely normal.

The checksums here are computed **by hand** in the test, byte by byte, not
taken from what the code produced. A test that asserted the encoder against
itself would pass forever on a shared misreading — which nearly happened:
this author hand-wrote a Logon claiming to be a published example, got the
body length wrong by three, and `simplefix` was right.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from treble.ems.session import (
    ChecksumError,
    NotLoggedOnError,
    SequenceGapError,
    Session,
    verify_checksum,
)

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def _hand_checksum(raw: bytes) -> str:
    """The checksum, computed here rather than read from the encoder.

    Independent by construction: sums every byte up to and including the SOH
    before `10=`, mod 256, three digits. This is the arithmetic FIX
    specifies, written out so the assertion does not depend on the code it
    is checking.
    """
    index = raw.rfind(b"\x0110=")
    return f"{sum(raw[: index + 1]) % 256:03d}"


def _session() -> Session:
    return Session(sender="TREBLE", target="SIM")


class TestTheBytesAreRight:
    def test_the_checksum_matches_a_hand_computation(self) -> None:
        raw = _session().logon(now=NOW)
        stated = raw.rsplit(b"\x0110=", 1)[1].rstrip(b"\x01").decode()
        assert stated == _hand_checksum(raw)

    def test_the_body_length_counts_the_bytes_between_the_fields(self) -> None:
        """BodyLength covers everything after `9=nn<SOH>` up to and including
        the SOH before `10=`. Counted here rather than trusted, because a
        wrong body length is the error that started this file."""
        raw = _session().logon(now=NOW)
        stated = int(raw.split(b"\x019=", 1)[1].split(b"\x01", 1)[0])
        after_header = raw.split(b"\x01", 2)[2]
        body = after_header[: after_header.rfind(b"\x0110=") + 1]
        assert stated == len(body)

    def test_a_logon_carries_the_negotiated_heartbeat(self) -> None:
        raw = _session().logon(now=NOW)
        assert b"\x01108=30\x01" in raw
        assert b"\x0135=A\x01" in raw

    def test_a_tampered_message_is_refused(self) -> None:
        """The one field that says whether the other fields can be believed.
        A corrupted tag in an otherwise well-formed message parses cleanly
        and means something else."""
        raw = _session().logon(now=NOW)
        tampered = raw.replace(b"\x01108=30\x01", b"\x01108=31\x01")
        with pytest.raises(ChecksumError, match="does not match"):
            verify_checksum(tampered)

    def test_a_message_with_no_checksum_is_refused(self) -> None:
        with pytest.raises(ChecksumError, match="no CheckSum"):
            verify_checksum(b"8=FIX.4.4\x0135=A\x01")


class TestSequenceNumbersAreNeverAbsorbed:
    """The failure this whole layer exists to prevent."""

    def test_a_gap_raises_and_names_both_numbers(self) -> None:
        peer = Session(sender="SIM", target="TREBLE")
        client = _session()
        client.receive(peer.logon(now=NOW))
        peer.outbound_seq = 5  # the peer skipped 2, 3 and 4
        with pytest.raises(SequenceGapError) as caught:
            client.receive(peer.heartbeat(now=NOW))
        assert caught.value.expected == 2
        assert caught.value.received == 5
        assert "3 message(s) missing" in str(caught.value)

    def test_the_counter_does_not_advance_past_a_gap(self) -> None:
        """So a caller that catches the gap and requests a resend still has
        the session pointing at the first missing message. Advancing would
        make the gap unrecoverable while reporting it."""
        peer = Session(sender="SIM", target="TREBLE")
        client = _session()
        client.receive(peer.logon(now=NOW))
        peer.outbound_seq = 5
        with pytest.raises(SequenceGapError):
            client.receive(peer.heartbeat(now=NOW))
        assert client.inbound_seq == 2

    def test_a_gap_is_never_merely_logged(self) -> None:
        """Stated as a property rather than an implementation detail: there
        is no code path in which an out-of-order message is accepted."""
        peer = Session(sender="SIM", target="TREBLE")
        client = _session()
        client.receive(peer.logon(now=NOW))
        peer.outbound_seq = 99
        with pytest.raises(SequenceGapError):
            client.receive(peer.heartbeat(now=NOW))

    def test_a_duplicate_is_accepted_without_advancing(self) -> None:
        """FIX permits duplicates during a resend. Rejecting them would
        reject a legitimate recovery, which is the opposite failure."""
        peer = Session(sender="SIM", target="TREBLE")
        client = _session()
        first = peer.logon(now=NOW)
        client.receive(first)
        assert client.inbound_seq == 2
        client.receive(first)  # the same message again
        assert client.inbound_seq == 2

    def test_each_sent_message_consumes_exactly_one_number(self) -> None:
        """A reused number is worse than a gap: the counterparty silently
        discards the second message, so the loss is invisible on both
        sides."""
        session = _session()
        session.logon(now=NOW)
        session.heartbeat(now=NOW)
        session.heartbeat(now=NOW)
        assert session.outbound_seq == 4
        assert sorted(session.sent) == [1, 2, 3]


class TestLogonComesFirst:
    def test_a_business_message_before_logon_is_refused(self) -> None:
        """A peer sending business messages without a Logon is either
        misconfigured or not the peer you think it is, and accepting a fill
        from it would be trusting an unauthenticated counterparty with a
        position."""
        peer = Session(sender="SIM", target="TREBLE")
        client = _session()
        with pytest.raises(NotLoggedOnError, match="before Logon"):
            client.receive(peer.heartbeat(now=NOW))

    def test_logon_itself_is_allowed_before_logon(self) -> None:
        peer = Session(sender="SIM", target="TREBLE")
        client = _session()
        client.receive(peer.logon(now=NOW))
        assert client.logged_on

    def test_the_heartbeat_interval_is_taken_from_the_peer(self) -> None:
        """Both sides must agree or one will declare the other dead. Taking
        the peer's value is what 'negotiated' means."""
        peer = Session(sender="SIM", target="TREBLE", heartbeat_seconds=45)
        client = _session()
        client.receive(peer.logon(now=NOW))
        assert client.heartbeat_seconds == 45


class TestTestRequests:
    def test_a_heartbeat_answering_a_test_request_echoes_its_id(self) -> None:
        """A TestRequest asks "are you there *now*". A heartbeat without the
        id could be a scheduled one that crossed in flight, so a live
        session and a dead one look alike for one interval."""
        raw = _session().heartbeat(now=NOW, test_req_id="ARE-YOU-THERE")
        assert b"\x01112=ARE-YOU-THERE\x01" in raw

    def test_an_unsolicited_heartbeat_carries_no_id(self) -> None:
        raw = _session().heartbeat(now=NOW)
        assert b"\x01112=" not in raw
