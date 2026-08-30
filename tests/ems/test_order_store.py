"""Orders recorded as facts, from the messages that placed them.

Distinct from `test_orders.py`, which covers the order *state machine* —
cancel, replace, and what must be refused. This covers the *record*: what
was asked for, dated by when it was asked.

The arrival time is the field that matters. An arrival-price benchmark is
measured from the instant the trader decided, so a parse that took the
acceptor's read time instead would produce a number that varies with
network scheduling and looks like execution quality.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import simplefix

from treble.ems.orders import (
    ORDER_FIELDS,
    NotAnOrderError,
    Order,
    order_facts,
    order_provenance,
    order_subject,
    parse_order,
)
from treble.ems.session import Session
from treble.ems.simulator import new_order_single

NOW = datetime(2026, 8, 29, 14, 30, tzinfo=UTC)


def _order(**kwargs: object) -> bytes:
    """A real NewOrderSingle, built by the encoder both sides use."""
    defaults: dict[str, object] = {
        "order_id": "ORD1",
        "symbol": "IBM",
        "side": "1",
        "quantity": 1000.0,
        "price": 250.0,
    }
    session = Session(sender="TREBLE", target="SIM")
    return new_order_single(session, now=NOW, **{**defaults, **kwargs})  # type: ignore[arg-type]


def _hand_built(**pairs: str) -> bytes:
    message = simplefix.FixMessage()
    base = {
        "8": "FIX.4.4",
        "35": "D",
        "11": "ORD1",
        "55": "IBM",
        "54": "1",
        "38": "100",
        "40": "2",
        "44": "250",
    }
    for tag, value in {**base, **pairs}.items():
        if value:
            message.append_pair(int(tag), value)
    return message.encode()


class TestParsingARealOrder:
    def test_a_new_order_single_parses(self) -> None:
        order = parse_order(_order())
        assert (order.order_id, order.symbol, order.side) == ("ORD1", "IBM", "buy")
        assert (order.quantity, order.limit_price) == (1000.0, 250.0)
        assert order.order_type == "limit"

    def test_a_sell_parses_as_a_sell(self) -> None:
        assert parse_order(_order(side="2")).side == "sell"

    def test_the_subject_is_the_client_order_id(self) -> None:
        """Keyed on ClOrdID because that is what an execution report echoes
        back, so the join is on the identifier the venue itself uses."""
        assert str(order_subject("ORD1")) == "order:ORD1"

    def test_parsing_is_deterministic(self) -> None:
        raw = _order()
        assert parse_order(raw) == parse_order(raw)


class TestArrivalTime:
    def test_transact_time_is_preferred(self) -> None:
        """The moment the trader decided, which is what an arrival-price
        benchmark is measured from."""
        assert parse_order(_order()).arrival_time == NOW

    def test_sending_time_is_the_fallback(self) -> None:
        """Closer to the decision than the acceptor's read, and the session
        layer stamps it on every message."""
        raw = _hand_built(**{"60": "", "52": "20260829-14:31:00"})
        assert parse_order(raw).arrival_time == datetime(2026, 8, 29, 14, 31, tzinfo=UTC)

    def test_received_at_is_the_last_resort(self) -> None:
        later = NOW + timedelta(hours=1)
        assert (
            parse_order(_hand_built(**{"60": "", "52": ""}), received_at=later).arrival_time
            == later
        )

    def test_the_preference_order_is_decision_then_transmission(self) -> None:
        """TransactTime wins over SendingTime when both are present. A
        message queued for a minute before sending would otherwise date the
        decision to when the socket drained."""
        raw = _hand_built(**{"60": "20260829-14:30:00", "52": "20260829-14:35:00"})
        assert parse_order(raw).arrival_time == NOW

    def test_no_time_anywhere_is_refused(self) -> None:
        with pytest.raises(NotAnOrderError, match="no TransactTime"):
            parse_order(_hand_built(**{"60": "", "52": ""}))

    def test_the_stamp_is_read_as_utc(self) -> None:
        assert parse_order(_order()).arrival_time.tzinfo is not None


class TestWhatIsRefused:
    def test_an_execution_report_is_not_an_order(self) -> None:
        assert parse_order  # imported
        with pytest.raises(NotAnOrderError, match="not a new order"):
            parse_order(_hand_built(**{"35": "8"}))

    def test_truncated_bytes_are_refused(self) -> None:
        with pytest.raises(NotAnOrderError, match="not a complete FIX message"):
            parse_order(b"8=FIX.4.4\x0135=D\x01")

    def test_a_market_order_is_refused_not_recorded_at_zero(self) -> None:
        """**The expensive one.** A market order carries no price, and
        recording it would put a limit of 0.0 in the book — against which
        every fill scores as infinitely bad."""
        with pytest.raises(NotAnOrderError, match="unmapped OrdType"):
            parse_order(_hand_built(**{"40": "1", "44": ""}))

    def test_an_unmapped_side_is_refused(self) -> None:
        with pytest.raises(NotAnOrderError, match="unmapped Side"):
            parse_order(_hand_built(**{"54": "7"}))

    def test_an_order_with_no_quantity_is_refused(self) -> None:
        with pytest.raises(NotAnOrderError, match="no OrderQty/Price"):
            parse_order(_hand_built(**{"38": ""}))

    def test_a_zero_limit_is_refused(self) -> None:
        with pytest.raises(ValueError, match=r"limit price 0\.0 is not positive"):
            Order(
                order_id="O1",
                symbol="IBM",
                side="buy",
                quantity=10.0,
                order_type="limit",
                limit_price=0.0,
                arrival_time=NOW,
            )

    def test_a_zero_quantity_is_refused(self) -> None:
        with pytest.raises(ValueError, match=r"quantity 0\.0 is not positive"):
            Order(
                order_id="O1",
                symbol="IBM",
                side="buy",
                quantity=0.0,
                order_type="limit",
                limit_price=250.0,
                arrival_time=NOW,
            )

    def test_a_naive_arrival_time_is_refused(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            Order(
                order_id="O1",
                symbol="IBM",
                side="buy",
                quantity=10.0,
                order_type="limit",
                limit_price=250.0,
                # Deliberately naive: this asserts the refusal.
                arrival_time=datetime(2026, 8, 29, 14, 30),  # noqa: DTZ001
            )


class TestFacts:
    def test_every_declared_field_is_written(self) -> None:
        facts = order_facts(parse_order(_order()), "p" * 64)
        assert {f.field for f in facts} == set(ORDER_FIELDS)

    def test_arrival_keeps_its_time_of_day(self) -> None:
        """Stored as text, not a date: the whole point is the instant, and a
        date-typed value would round the decision to midnight."""
        facts = {f.field: f.value for f in order_facts(parse_order(_order()), "p" * 64)}
        assert facts["ems:order:arrival"] == NOW.isoformat()

    def test_an_order_is_an_instant_not_an_open_period(self) -> None:
        for fact in order_facts(parse_order(_order()), "p" * 64):
            assert fact.effective_to == fact.effective_from == NOW.date()

    def test_all_facts_share_one_subject(self) -> None:
        facts = order_facts(parse_order(_order()), "p" * 64)
        assert len({f.subject for f in facts}) == 1


class TestProvenance:
    def test_it_carries_the_archived_message_key(self) -> None:
        record = order_provenance("a" * 64, received_at=NOW, source_uri="fix://SIM/TREBLE")
        assert record.payload_hash == "a" * 64

    def test_provenance_without_a_payload_is_refused(self) -> None:
        with pytest.raises(ValueError, match="needs the archived message"):
            order_provenance("", received_at=NOW, source_uri="fix://SIM/TREBLE")


def test_sides_are_shared_with_the_execution_parser() -> None:
    """A side mapped one way on the order and another on the fill would make
    the join silently wrong, so both read one mapping."""
    from treble.ems.executions import SIDES as EXECUTION_SIDES
    from treble.ems.orders import SIDES as ORDER_SIDES

    assert ORDER_SIDES is EXECUTION_SIDES
