"""Order entry against the in-repo simulator (P3_3).

A simulator this author wrote, driven by a client this author wrote, is a
closed loop — both halves could share one misreading of FIX and agree
forever. Three things break it, and the third is what this file exercises:

* `simplefix` encodes and parses both sides, an outside implementation of
  BodyLength and CheckSum which has already been right where a hand-written
  message here was wrong;
* `test_session.py` asserts checksums computed by hand rather than by the
  encoder;
* **the simulator can be told to misbehave** — to skip a sequence number or
  corrupt a checksum — so the client's refusals are exercised rather than
  assumed. A simulator that only ever behaves well tests the happy path
  twice and calls it coverage.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from treble.ems.session import ChecksumError, SequenceGapError, Session
from treble.ems.simulator import (
    EXECUTION_REPORT,
    FILLED,
    Simulator,
    _decimal,
    new_order_single,
)

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def _connected(**kwargs: object) -> tuple[Session, Simulator]:
    client = Session(sender="TREBLE", target="SIM")
    simulator = Simulator(**kwargs)  # type: ignore[arg-type]
    for reply in simulator.respond(client.logon(now=NOW), now=NOW):
        client.receive(reply)
    return client, simulator


def _order(client: Session, **kwargs: object) -> bytes:
    defaults: dict[str, object] = {
        "order_id": "ORD1",
        "symbol": "IBM",
        "side": "1",
        "quantity": 1_000_000.0,
        "price": 98.5,
    }
    return new_order_single(client, now=NOW, **{**defaults, **kwargs})  # type: ignore[arg-type]


class TestTheRoundTrip:
    def test_logon_is_answered_and_both_sides_agree_on_sequence(self) -> None:
        client, simulator = _connected()
        assert client.logged_on
        assert client.inbound_seq == simulator.session.outbound_seq
        assert client.outbound_seq == simulator.session.inbound_seq

    def test_an_order_comes_back_as_a_complete_fill(self) -> None:
        client, simulator = _connected()
        replies = simulator.respond(_order(client), now=NOW)
        report = client.receive(replies[0])
        assert report.get(35).decode() == EXECUTION_REPORT
        assert report.get(39).decode() == FILLED
        assert report.get(11).decode() == "ORD1"
        assert report.get(151).decode() == "0"  # nothing left

    def test_the_fill_price_and_quantity_are_the_order_s_own(self) -> None:
        client, simulator = _connected()
        report = client.receive(
            simulator.respond(_order(client, quantity=250_000.0, price=101.25), now=NOW)[0]
        )
        assert report.get(32).decode() == "250000"
        assert report.get(31).decode() == "101.25"

    def test_a_million_is_not_sent_in_scientific_notation(self) -> None:
        """`f"{1_000_000:g}"` is `1e+06`, which is not a FIX quantity: a
        venue rejects it or reads it as something else, and a one-million
        order arriving as anything but one million is the worst outcome on
        this path. Found by reading `32=1e+06` off a fill."""
        assert _decimal(1_000_000.0) == "1000000"
        assert _decimal(1e9) == "1000000000"
        assert "e" not in _decimal(1e12)

    def test_prices_keep_their_fractions(self) -> None:
        """The other half of the same formatter: a bond price of 98.5 must
        not become 98 or 98.50000000."""
        assert _decimal(98.5) == "98.5"
        assert _decimal(0.0625) == "0.0625"


class TestTheSimulatorCanMisbehave:
    """Without this the client's refusals are untested assertions."""

    def test_a_skipped_sequence_number_is_caught_by_the_client(self) -> None:
        """The simulator drops its second outbound message, which is the
        Logon reply's successor — a venue does this, and the client must
        refuse rather than continue with a hole in the stream."""
        client, simulator = _connected(skip_outbound=frozenset({2}))
        first = simulator.respond(_order(client), now=NOW)
        assert first == [], "the simulator was asked to drop this one"
        # The next message therefore arrives with number 3 where 2 was due.
        later = simulator.respond(_order(client, order_id="ORD2"), now=NOW)
        with pytest.raises(SequenceGapError) as caught:
            client.receive(later[0])
        assert caught.value.expected == 2
        assert caught.value.received == 3

    def test_a_corrupt_checksum_is_caught_by_the_client(self) -> None:
        client, simulator = _connected(corrupt_outbound=frozenset({2}))
        replies = simulator.respond(_order(client), now=NOW)
        with pytest.raises(ChecksumError, match="does not match"):
            client.receive(replies[0])

    def test_a_well_behaved_simulator_still_passes(self) -> None:
        """The control. If the hostile cases failed because the harness is
        broken rather than because the client refuses, this fails too."""
        client, simulator = _connected()
        client.receive(simulator.respond(_order(client), now=NOW)[0])
        assert simulator.fills == 1


class TestResend:
    def test_a_resend_request_replays_the_original_bytes(self) -> None:
        """Re-encoding would give the message a new sending time and a new
        checksum — a different message wearing the same sequence number,
        which is what a resend must never be."""
        client, simulator = _connected()
        first = simulator.respond(_order(client), now=NOW)[0]
        client.receive(first)
        replayed = simulator.respond(client.resend_request(begin=2, end=2, now=NOW), now=NOW)
        assert replayed == [first]

    def test_a_resend_of_an_unknown_range_returns_nothing_rather_than_inventing(self) -> None:
        client, simulator = _connected()
        assert simulator.respond(client.resend_request(begin=90, end=95, now=NOW), now=NOW) == []


class TestTheSimulatorIsHonestAboutWhatItIsNot:
    def test_it_fills_completely_and_immediately(self) -> None:
        """Recorded as a property so nobody mistakes it for venue
        behaviour. Partial fills, queue position and rejects are the venue's;
        inventing plausible versions here would produce execution reports
        nobody should analyse."""
        client, simulator = _connected()
        report = client.receive(simulator.respond(_order(client), now=NOW)[0])
        assert report.get(14).decode() == report.get(38).decode()  # cum == ordered
        assert report.get(151).decode() == "0"
