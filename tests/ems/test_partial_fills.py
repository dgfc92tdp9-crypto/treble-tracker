"""Partial fills, and what they do to cancel.

An order filled in slices is the ordinary case on any venue with size,
and it is where a client's position accounting goes wrong quietly. The
three ways it goes wrong:

* the client learns of the first print and not the second, so it carries
  a third of the position it actually holds;
* the venue refuses to cancel a part-filled order, so the trader cannot
  stop the remainder from trading;
* the cancel succeeds and takes the filled quantity with it, so the
  trader believes they are flat while holding stock.

None of those look like errors. Every one of them produces a position,
P&L and risk figure that agree with each other and with nothing else.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import simplefix

from treble.ems.session import Session
from treble.ems.simulator import (
    AVG_PX,
    CANCELED,
    CL_ORD_ID,
    CUM_QTY,
    EXECUTION_REPORT,
    FILLED,
    LAST_PX,
    LAST_QTY,
    LEAVES_QTY,
    ORD_STATUS,
    PARTIALLY_FILLED,
    Simulator,
    _slices,
    cancel_request,
    new_order_single,
)

NOW = datetime(2026, 8, 23, 14, 0, tzinfo=UTC)


def _connected(**kwargs: object) -> tuple[Session, Simulator]:
    """A client and simulator that have completed a logon.

    The reply is fed back to the client rather than discarded. Skipping
    it leaves the client's inbound counter at 1 while the simulator sends
    2, so the very next message raises a sequence gap — a test failure
    that looks like a defect in fills and is a defect in the fixture."""
    client = Session(sender="TREBLE", target="SIM")
    simulator = Simulator(**kwargs)  # type: ignore[arg-type]
    for reply in simulator.respond(client.logon(now=NOW), now=NOW):
        client.receive(reply)
    return client, simulator


def _parse(raw: bytes) -> simplefix.FixMessage:
    parser = simplefix.FixParser()
    parser.append_buffer(raw)
    message = parser.get_message()
    assert message is not None
    return message


def _field(raw: bytes, tag: int) -> str:
    return (_parse(raw).get(tag) or b"").decode()


def _send_order(client: Session, simulator: Simulator, quantity: float = 900.0) -> list[bytes]:
    order = new_order_single(
        client, order_id="O1", symbol="IBM", side="1", quantity=quantity, price=150.0, now=NOW
    )
    return simulator.respond(order, now=NOW)


class TestSlicedFills:
    def test_three_slices_produce_three_reports(self) -> None:
        client, simulator = _connected(fill_slices=3)
        replies = _send_order(client, simulator)
        assert len(replies) == 3
        assert [_field(r, ORD_STATUS) for r in replies] == [
            PARTIALLY_FILLED,
            PARTIALLY_FILLED,
            FILLED,
        ]

    def test_cumulative_quantity_climbs_and_leaves_falls(self) -> None:
        """The pair a client reconciles against. If CumQty restarted at
        each print, three fills of 300 would read as a 300 position."""
        client, simulator = _connected(fill_slices=3)
        replies = _send_order(client, simulator)
        assert [_field(r, CUM_QTY) for r in replies] == ["300", "600", "900"]
        assert [_field(r, LEAVES_QTY) for r in replies] == ["600", "300", "0"]

    def test_the_slices_sum_to_the_order(self) -> None:
        """100 into 3 does not divide. The remainder goes on the last
        slice rather than being spread, because spreading leaves a float
        residue and an order that never reaches filled."""
        client, simulator = _connected(fill_slices=3)
        replies = _send_order(client, simulator, quantity=100.0)
        assert sum(float(_field(r, LAST_QTY)) for r in replies) == pytest.approx(100.0)
        assert _field(replies[-1], ORD_STATUS) == FILLED
        assert _field(replies[-1], CUM_QTY) == "100"

    @pytest.mark.parametrize(
        ("quantity", "slices"),
        [(100.0, 3), (100.0, 6), (900.0, 7), (1e6, 7), (1e9, 7), (333.0, 11)],
    )
    def test_the_slices_accumulate_back_onto_the_order(self, quantity: float, slices: int) -> None:
        """Exactly, not approximately — the client reconciles CumQty
        against OrderQty and a residue leaves an order fully traded that
        never says so.

        `pytest.approx` would pass here against a broken split; that is
        how the first version of this test missed it. The parameters are
        the pairs where the three plausible constructions actually differ:
        an even split is inexact at 100/6, and dumping the remainder on
        the last slice is inexact at 900/7 and 1e9/7.
        """
        client, simulator = _connected(fill_slices=slices, fill_immediately=False)
        _send_order(client, simulator, quantity=quantity)
        for part in _slices(quantity, slices):
            simulator.fill("O1", part, now=NOW)
        resting = simulator.book["O1"]
        assert resting.filled == quantity, f"residue of {resting.filled - quantity!r}"
        assert resting.status == FILLED
        assert resting.leaves == 0.0

    @pytest.mark.parametrize(("quantity", "count"), [(1e7, 11), (1e9, 6), (1e9, 13)])
    def test_a_venues_own_fill_sizes_still_complete_the_order(
        self, quantity: float, count: int
    ) -> None:
        """The case an absolute tolerance gets wrong.

        Ten million shares in eleven fills accumulates 1.86e-9 *short* of
        the order — just past a fixed 1e-9 floor, at a size any
        institutional desk trades daily. Under an absolute tolerance that
        order sits at PARTIALLY_FILLED for ever: still cancellable after
        being fully executed, so a trader could cancel a completed order
        and believe they were flat.

        The parameters have to *undershoot*. The first version of this
        test used a billion in sevenths, which overshoots by 2.4e-7 and so
        satisfies `filled >= quantity - tolerance` however small the
        tolerance is — passing against exactly the bug it was written for.
        """
        client, simulator = _connected(fill_immediately=False)
        _send_order(client, simulator, quantity=quantity)
        for _ in range(count):
            simulator.fill("O1", quantity / count, now=NOW)
        assert simulator.book["O1"].filled < quantity, "these parameters must undershoot"
        assert simulator.book["O1"].status == FILLED
        assert simulator.book["O1"].leaves == 0.0

    def test_one_slice_is_still_a_single_fill(self) -> None:
        """The default. Adding slicing must not change what an unsliced
        venue looks like."""
        client, simulator = _connected()
        replies = _send_order(client, simulator)
        assert len(replies) == 1
        assert _field(replies[0], ORD_STATUS) == FILLED
        assert _field(replies[0], LEAVES_QTY) == "0"

    def test_every_report_is_an_execution_report(self) -> None:
        client, simulator = _connected(fill_slices=4)
        for raw in _send_order(client, simulator):
            assert _field(raw, 35) == EXECUTION_REPORT
            assert _field(raw, CL_ORD_ID) == "O1"


class TestAveragePrice:
    def test_slices_at_different_prices_average_by_quantity(self) -> None:
        """Not a mean of the prices. 100 at 150 and 900 at 100 averages
        105, not 125 — a client told 125 would misvalue the position by
        20%."""
        client, simulator = _connected(fill_immediately=False)
        _send_order(client, simulator, quantity=1000.0)
        simulator.fill("O1", 100.0, price=150.0, now=NOW)
        last = simulator.fill("O1", 900.0, price=100.0, now=NOW)
        assert float(_field(last, AVG_PX)) == pytest.approx(105.0)

    def test_the_last_price_is_the_slice_not_the_order(self) -> None:
        client, simulator = _connected(fill_immediately=False)
        _send_order(client, simulator, quantity=1000.0)
        raw = simulator.fill("O1", 100.0, price=149.5, now=NOW)
        assert float(_field(raw, LAST_PX)) == pytest.approx(149.5)


class TestCancellingAPartiallyFilledOrder:
    def test_it_can_be_cancelled(self) -> None:
        """The remainder is still working. A venue that refused would
        leave the trader unable to stop an order that is still trading."""
        client, simulator = _connected(fill_immediately=False)
        _send_order(client, simulator)
        simulator.fill("O1", 300.0, now=NOW)
        reply = simulator.respond(
            cancel_request(
                client, order_id="C1", original_id="O1", symbol="IBM", side="1", now=NOW
            ),
            now=NOW,
        )[0]
        assert _field(reply, ORD_STATUS) == CANCELED

    def test_the_cancel_keeps_the_filled_quantity(self) -> None:
        """The 300 already traded is a real position and survives the
        cancel. A cancel that zeroed CumQty would tell the trader they
        were flat while holding 300 shares."""
        client, simulator = _connected(fill_immediately=False)
        _send_order(client, simulator)
        simulator.fill("O1", 300.0, now=NOW)
        reply = simulator.respond(
            cancel_request(
                client, order_id="C1", original_id="O1", symbol="IBM", side="1", now=NOW
            ),
            now=NOW,
        )[0]
        assert _field(reply, CUM_QTY) == "300"

    def test_the_cancelled_remainder_is_not_still_working(self) -> None:
        client, simulator = _connected(fill_immediately=False)
        _send_order(client, simulator)
        simulator.fill("O1", 300.0, now=NOW)
        reply = simulator.respond(
            cancel_request(
                client, order_id="C1", original_id="O1", symbol="IBM", side="1", now=NOW
            ),
            now=NOW,
        )[0]
        assert _field(reply, LEAVES_QTY) == "0"

    def test_a_fully_filled_order_still_cannot_be_cancelled(self) -> None:
        """The refusal that already existed must survive the change that
        made part-filled orders cancellable. Widening `live` by one status
        too many is how it would be lost."""
        client, simulator = _connected(fill_immediately=False)
        _send_order(client, simulator)
        simulator.fill("O1", 900.0, now=NOW)
        reply = simulator.respond(
            cancel_request(
                client, order_id="C1", original_id="O1", symbol="IBM", side="1", now=NOW
            ),
            now=NOW,
        )[0]
        assert _field(reply, 35) != EXECUTION_REPORT
        assert "too late" in _field(reply, 58)


class TestTheToleranceDoesNotSwallowARealRemainder:
    """The other side of the tolerance, and the one that costs money.

    Slack wide enough to absorb float residue must be far too narrow to
    absorb a share. An order marked FILLED while a share is still working
    tells the client to stop watching it — and the venue keeps trading it.
    """

    @pytest.mark.parametrize("short_by", [1.0, 0.01, 0.0001])
    def test_an_order_short_by_a_tradeable_amount_is_not_filled(self, short_by: float) -> None:
        client, simulator = _connected(fill_immediately=False)
        _send_order(client, simulator, quantity=1e6)
        raw = simulator.fill("O1", 1e6 - short_by, now=NOW)
        assert _field(raw, ORD_STATUS) == PARTIALLY_FILLED
        assert simulator.book["O1"].leaves == pytest.approx(short_by)
        assert simulator.book["O1"].live

    def test_the_remainder_can_still_be_filled(
        self,
    ) -> None:
        """And completing it works, so the refusal above is a live order
        rather than a stuck one."""
        client, simulator = _connected(fill_immediately=False)
        _send_order(client, simulator, quantity=1e6)
        simulator.fill("O1", 1e6 - 1.0, now=NOW)
        assert _field(simulator.fill("O1", 1.0, now=NOW), ORD_STATUS) == FILLED


class TestOverfillingIsRefused:
    def test_a_fill_beyond_the_remainder_raises(self) -> None:
        """A venue reporting more than was ordered is a bug. Clamping it
        quietly would hide from the client exactly the case where it ends
        up holding more than it asked for."""
        client, simulator = _connected(fill_immediately=False)
        _send_order(client, simulator)
        simulator.fill("O1", 600.0, now=NOW)
        with pytest.raises(ValueError, match="exceeds 300"):
            simulator.fill("O1", 400.0, now=NOW)

    def test_a_finished_order_cannot_be_filled_again(self) -> None:
        client, simulator = _connected(fill_immediately=False)
        _send_order(client, simulator)
        simulator.fill("O1", 900.0, now=NOW)
        with pytest.raises(ValueError, match="not working"):
            simulator.fill("O1", 1.0, now=NOW)

    def test_the_exact_remainder_is_allowed(self) -> None:
        """The boundary. A guard written with `>=` would refuse the fill
        that completes the order, so no order could ever finish."""
        client, simulator = _connected(fill_immediately=False)
        _send_order(client, simulator)
        simulator.fill("O1", 600.0, now=NOW)
        assert _field(simulator.fill("O1", 300.0, now=NOW), ORD_STATUS) == FILLED


class TestTheClientSeesEveryPrint:
    def test_sequence_numbers_are_consecutive_across_slices(self) -> None:
        """Three reports consume three numbers. If the simulator emitted
        them under one, the client would discard two as duplicates and
        carry a third of the position."""
        client, simulator = _connected(fill_slices=3)
        replies = _send_order(client, simulator)
        numbers = [int(_field(r, 34)) for r in replies]
        assert numbers == [numbers[0], numbers[0] + 1, numbers[0] + 2]

    def test_the_client_accepts_all_three(self) -> None:
        client, simulator = _connected(fill_slices=3)
        for raw in _send_order(client, simulator):
            client.receive(raw)
        assert client.inbound_seq == 5  # logon + three reports, next expected
