"""The order state machine: cancel, replace, and what must be refused.

**A cancel that arrives after the fill must be rejected, never
acknowledged.** A trader who believes an order was cancelled when it was
filled is long or short something they think they are flat, and every
downstream number — position, P&L, risk — agrees with them. That is the
single most expensive thing this file prevents, and it is one `if`.

The simulator can be told to rest orders instead of filling them, because a
simulator that filled everything instantly would make every cancel arrive
too late: the refusals would all pass, for the wrong reason.
"""

from __future__ import annotations

from datetime import UTC, datetime

from treble.ems.session import Session
from treble.ems.simulator import (
    CANCEL_REJECT,
    CANCELED,
    EXECUTION_REPORT,
    FILLED,
    NEW,
    REPLACED,
    Simulator,
    cancel_replace_request,
    cancel_request,
    new_order_single,
)

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def _connected(**kwargs: object) -> tuple[Session, Simulator]:
    client = Session(sender="TREBLE", target="SIM")
    simulator = Simulator(**kwargs)  # type: ignore[arg-type]
    for reply in simulator.respond(client.logon(now=NOW), now=NOW):
        client.receive(reply)
    return client, simulator


def _send(client: Session, simulator: Simulator, raw: bytes) -> object:
    return client.receive(simulator.respond(raw, now=NOW)[0])


def _order(client: Session, simulator: Simulator, order_id: str = "O1", **kwargs: object) -> object:
    defaults: dict[str, object] = {
        "symbol": "IBM",
        "side": "1",
        "quantity": 1_000_000.0,
        "price": 98.5,
    }
    return _send(
        client,
        simulator,
        new_order_single(client, order_id=order_id, now=NOW, **{**defaults, **kwargs}),  # type: ignore[arg-type]
    )


class TestCancelAfterFillIsRefused:
    """The money-losing race, and the reason this state machine exists."""

    def test_cancelling_a_filled_order_is_rejected(self) -> None:
        client, simulator = _connected()  # fills immediately
        _order(client, simulator, "F1", quantity=100.0)
        reject = _send(
            client,
            simulator,
            cancel_request(
                client, order_id="F2", original_id="F1", symbol="IBM", side="1", now=NOW
            ),
        )
        assert reject.get(35).decode() == CANCEL_REJECT  # type: ignore[attr-defined]
        assert "too late to cancel" in reject.get(58).decode()  # type: ignore[attr-defined]

    def test_the_rejection_says_what_state_the_order_reached(self) -> None:
        """A rejection carrying no reason is one a trader cannot act on:
        they do not know whether to retry, to resend under a new id, or to
        check their position because the order already filled."""
        client, simulator = _connected()
        _order(client, simulator, "F1", quantity=100.0)
        reject = _send(
            client,
            simulator,
            cancel_request(
                client, order_id="F2", original_id="F1", symbol="IBM", side="1", now=NOW
            ),
        )
        assert f"already {FILLED}" in reject.get(58).decode()  # type: ignore[attr-defined]

    def test_a_filled_order_is_not_silently_reopened(self) -> None:
        client, simulator = _connected()
        _order(client, simulator, "F1", quantity=100.0)
        _send(
            client,
            simulator,
            cancel_request(
                client, order_id="F2", original_id="F1", symbol="IBM", side="1", now=NOW
            ),
        )
        assert simulator.book["F1"].status == FILLED


class TestRestingOrders:
    def test_an_order_rests_when_the_simulator_is_not_filling(self) -> None:
        client, simulator = _connected(fill_immediately=False)
        report = _order(client, simulator)
        assert report.get(150).decode() == NEW  # type: ignore[attr-defined]
        assert report.get(151).decode() == "1000000"  # type: ignore[attr-defined]

    def test_a_resting_order_can_be_cancelled(self) -> None:
        client, simulator = _connected(fill_immediately=False)
        _order(client, simulator)
        report = _send(
            client,
            simulator,
            cancel_request(
                client, order_id="O2", original_id="O1", symbol="IBM", side="1", now=NOW
            ),
        )
        assert report.get(150).decode() == CANCELED  # type: ignore[attr-defined]

    def test_cancelling_twice_is_rejected(self) -> None:
        client, simulator = _connected(fill_immediately=False)
        _order(client, simulator)
        for order_id in ("O2", "O3"):
            reply = _send(
                client,
                simulator,
                cancel_request(
                    client, order_id=order_id, original_id="O1", symbol="IBM", side="1", now=NOW
                ),
            )
        assert reply.get(35).decode() == CANCEL_REJECT  # type: ignore[attr-defined]

    def test_cancelling_an_unknown_order_is_rejected(self) -> None:
        client, simulator = _connected(fill_immediately=False)
        reject = _send(
            client,
            simulator,
            cancel_request(
                client, order_id="X2", original_id="NOSUCH", symbol="IBM", side="1", now=NOW
            ),
        )
        assert reject.get(35).decode() == CANCEL_REJECT  # type: ignore[attr-defined]
        assert "unknown order" in reject.get(58).decode()  # type: ignore[attr-defined]


class TestReplace:
    def test_a_replace_amends_quantity_and_price(self) -> None:
        client, simulator = _connected(fill_immediately=False)
        _order(client, simulator)
        report = _send(
            client,
            simulator,
            cancel_replace_request(
                client,
                order_id="O2",
                original_id="O1",
                symbol="IBM",
                side="1",
                quantity=500_000.0,
                price=98.25,
                now=NOW,
            ),
        )
        assert report.get(150).decode() == REPLACED  # type: ignore[attr-defined]
        assert report.get(38).decode() == "500000"  # type: ignore[attr-defined]
        assert report.get(41).decode() == "O1"  # type: ignore[attr-defined]

    def test_the_original_leaves_the_book_so_a_later_cancel_is_unambiguous(self) -> None:
        """Keeping both live would make a later cancel answerable by two
        orders, which is the defect the duplicate-id check exists to
        prevent."""
        client, simulator = _connected(fill_immediately=False)
        _order(client, simulator)
        _send(
            client,
            simulator,
            cancel_replace_request(
                client,
                order_id="O2",
                original_id="O1",
                symbol="IBM",
                side="1",
                quantity=500_000.0,
                price=98.25,
                now=NOW,
            ),
        )
        assert simulator.book["O1"].status == REPLACED
        assert simulator.book["O2"].live

    def test_replacing_below_what_is_filled_is_refused(self) -> None:
        """Reducing below the executed quantity is not an amendment, it is
        an instruction to un-execute. Refused rather than clamped: a silent
        clamp leaves the trader believing a smaller position than they
        hold."""
        client, simulator = _connected(fill_immediately=False)
        _order(client, simulator)
        simulator.book["O1"].filled = 750_000.0  # a partial arrived
        reject = _send(
            client,
            simulator,
            cancel_replace_request(
                client,
                order_id="O2",
                original_id="O1",
                symbol="IBM",
                side="1",
                quantity=500_000.0,
                price=98.25,
                now=NOW,
            ),
        )
        assert reject.get(35).decode() == CANCEL_REJECT  # type: ignore[attr-defined]
        assert "below 750000 already filled" in reject.get(58).decode()  # type: ignore[attr-defined]

    def test_a_replace_carries_the_filled_quantity_forward(self) -> None:
        """The replacement is the same order amended, not a new one. Losing
        the fill would reset cumulative quantity to zero and report a
        position the trader does not have."""
        client, simulator = _connected(fill_immediately=False)
        _order(client, simulator)
        simulator.book["O1"].filled = 250_000.0
        _send(
            client,
            simulator,
            cancel_replace_request(
                client,
                order_id="O2",
                original_id="O1",
                symbol="IBM",
                side="1",
                quantity=800_000.0,
                price=98.25,
                now=NOW,
            ),
        )
        assert simulator.book["O2"].filled == 250_000.0


class TestIdentity:
    def test_a_duplicate_order_id_is_rejected(self) -> None:
        """A ClOrdID must be unique per session. Two orders answering to one
        name make every later cancel ambiguous."""
        client, simulator = _connected(fill_immediately=False)
        _order(client, simulator, "O1")
        reject = _order(client, simulator, "O1")
        assert reject.get(35).decode() == CANCEL_REJECT  # type: ignore[attr-defined]
        assert "duplicate ClOrdID" in reject.get(58).decode()  # type: ignore[attr-defined]

    def test_a_normal_order_is_still_an_execution_report(self) -> None:
        """The control: if the rejections above fire on everything, this
        fails too."""
        client, simulator = _connected(fill_immediately=False)
        assert _order(client, simulator).get(35).decode() == EXECUTION_REPORT  # type: ignore[attr-defined]
