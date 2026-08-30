"""Executions parsed out of the FIX messages that reported them.

The dangerous direction here is counting something as a fill that is not
one. An execution report is *not* an execution: acks, cancels and rejects
all arrive as 35=8, and a TCA average that includes a cancel at price 0 is
wrong in a way that looks like a good execution.

So most of what follows is refusals. The parser is driven with real
messages from the simulator rather than hand-built strings wherever
possible, because a parser tested only against its author's idea of the
format agrees with itself.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import simplefix

from treble.ems.executions import (
    EXECUTION_FIELDS,
    SIDES,
    TRADE_EXEC_TYPES,
    Execution,
    NotAnExecutionError,
    execution_facts,
    execution_provenance,
    execution_subject,
    parse_execution,
)
from treble.ems.session import Session
from treble.ems.simulator import Simulator, new_order_single

NOW = datetime(2026, 8, 29, 14, 30, tzinfo=UTC)


def _connected(**kwargs: object) -> tuple[Session, Simulator]:
    """A logged-on client and simulator, the way `test_simulator.py` does it.

    Through the real handshake rather than a hand-built message at sequence
    2: the session layer refuses a gap, and a fixture that sidestepped it
    would be testing this parser against bytes no session would deliver.
    """
    client = Session(sender="TREBLE", target="SIM")
    simulator = Simulator(**kwargs)  # type: ignore[arg-type]
    for reply in simulator.respond(client.logon(now=NOW), now=NOW):
        client.receive(reply)
    return client, simulator


def _filled_report(**kwargs: object) -> bytes:
    """A real fill, produced by the simulator rather than hand-written."""
    defaults: dict[str, object] = {
        "order_id": "ORD1",
        "symbol": "IBM",
        "side": "1",
        "quantity": 100.0,
        "price": 250.0,
    }
    client, simulator = _connected(fill_immediately=True)
    order = new_order_single(client, now=NOW, **{**defaults, **kwargs})  # type: ignore[arg-type]
    return list(simulator.respond(order, now=NOW))[-1]


class TestParsingARealFill:
    def test_a_simulator_fill_parses(self) -> None:
        execution = parse_execution(_filled_report())
        assert execution.symbol == "IBM"
        assert execution.side == "buy"
        assert execution.last_qty == 100
        assert execution.last_px == 250.0

    def test_the_subject_is_the_venues_exec_id(self) -> None:
        """Keyed on ExecID so re-recording the same report collapses in the
        visibility window instead of double-counting the fill."""
        execution = parse_execution(_filled_report())
        assert str(execution_subject(execution.exec_id)).startswith("exec:")
        assert execution.exec_id in str(execution_subject(execution.exec_id))

    def test_a_sell_parses_as_a_sell(self) -> None:
        execution = parse_execution(_filled_report(side="2"))
        assert execution.side == "sell"
        assert execution.signed_qty == -100

    def test_notional_is_quantity_times_price(self) -> None:
        execution = parse_execution(_filled_report(quantity=10.0, price=99.5))
        assert execution.notional == pytest.approx(995.0)


class TestWhatIsRefused:
    """An execution report that reports no trade must not become a fill."""

    def test_an_acknowledgement_is_not_a_fill(self) -> None:
        """The defect this exists to prevent: a resting order's ack is 35=8
        with LastQty 0, and counting it averages a zero price into TCA."""
        client, simulator = _connected(fill_immediately=False)
        order = new_order_single(
            client,
            now=NOW,
            order_id="ORD1",
            symbol="IBM",
            side="1",
            quantity=100.0,
            price=250.0,
        )
        ack = list(simulator.respond(order, now=NOW))[-1]
        with pytest.raises(NotAnExecutionError, match="reports no trade"):
            parse_execution(ack)

    def test_a_non_execution_message_is_refused(self) -> None:
        client, _ = _connected()
        with pytest.raises(NotAnExecutionError, match="not an execution report"):
            parse_execution(client.logon(now=NOW))

    def test_truncated_bytes_are_refused(self) -> None:
        with pytest.raises(NotAnExecutionError, match="not a complete FIX message"):
            parse_execution(b"8=FIX.4.4\x0135=8\x01")

    def test_an_unmapped_side_is_refused_not_guessed(self) -> None:
        """A short sale booked as a buy is a position error with a
        plausible explanation, which is the kind that survives review."""
        message = simplefix.FixMessage()
        for tag, value in (
            (8, "FIX.4.4"),
            (35, "8"),
            (17, "E1"),
            (150, "F"),
            (11, "O1"),
            (55, "IBM"),
            (54, "7"),
            (32, "10"),
            (31, "100"),
            (60, "20260829-14:30:00"),
        ):
            message.append_pair(tag, value)
        with pytest.raises(NotAnExecutionError, match="unmapped Side"):
            parse_execution(message.encode())

    def test_a_trade_with_no_price_is_refused(self) -> None:
        message = simplefix.FixMessage()
        for tag, value in (
            (8, "FIX.4.4"),
            (35, "8"),
            (17, "E1"),
            (150, "F"),
            (11, "O1"),
            (55, "IBM"),
            (54, "1"),
            (60, "20260829-14:30:00"),
        ):
            message.append_pair(tag, value)
        with pytest.raises(NotAnExecutionError, match="no LastQty/LastPx"):
            parse_execution(message.encode())

    def test_a_zero_quantity_trade_is_not_a_trade(self) -> None:
        with pytest.raises(ValueError, match="is not a trade"):
            Execution(
                exec_id="E1",
                order_id="O1",
                symbol="IBM",
                side="buy",
                last_qty=0.0,
                last_px=100.0,
                cum_qty=0.0,
                average_price=100.0,
                order_qty=100.0,
                transact_time=NOW,
            )

    def test_a_naive_transact_time_is_refused(self) -> None:
        """A naive stamp makes every point-in-time read of an execution
        depend on the reader's locale."""
        with pytest.raises(ValueError, match="timezone-aware"):
            Execution(
                exec_id="E1",
                order_id="O1",
                symbol="IBM",
                side="buy",
                last_qty=1.0,
                last_px=100.0,
                cum_qty=1.0,
                average_price=100.0,
                order_qty=1.0,
                # Deliberately naive: this asserts the refusal.
                transact_time=datetime(2026, 8, 29, 14, 30),  # noqa: DTZ001
            )


class TestTransactTime:
    def test_the_fix_stamp_is_read_as_utc(self) -> None:
        """FIX stamps carry no zone and are UTC by specification. Attaching
        it beats assuming it away."""
        execution = parse_execution(_filled_report())
        assert execution.transact_time.tzinfo is not None
        assert execution.transact_time.utcoffset() == UTC.utcoffset(None)

    def test_received_at_is_the_fallback_not_the_clock(self) -> None:
        """A parse that reads the wall clock cannot be replayed — the exact
        defect the nightly replay round trip exists to catch."""
        message = simplefix.FixMessage()
        for tag, value in (
            (8, "FIX.4.4"),
            (35, "8"),
            (17, "E1"),
            (150, "F"),
            (11, "O1"),
            (55, "IBM"),
            (54, "1"),
            (32, "10"),
            (31, "100"),
        ):
            message.append_pair(tag, value)
        execution = parse_execution(message.encode(), received_at=NOW)
        assert execution.transact_time == NOW

    def test_no_stamp_and_no_fallback_is_refused(self) -> None:
        message = simplefix.FixMessage()
        for tag, value in (
            (8, "FIX.4.4"),
            (35, "8"),
            (17, "E1"),
            (150, "F"),
            (11, "O1"),
            (55, "IBM"),
            (54, "1"),
            (32, "10"),
            (31, "100"),
        ):
            message.append_pair(tag, value)
        with pytest.raises(NotAnExecutionError, match="no TransactTime"):
            parse_execution(message.encode())

    def test_parsing_is_deterministic(self) -> None:
        """Same bytes, same Execution — twice. What makes the vault
        replayable into a store."""
        raw = _filled_report()
        assert parse_execution(raw) == parse_execution(raw)


class TestFacts:
    def test_every_declared_field_is_written(self) -> None:
        execution = parse_execution(_filled_report())
        facts = execution_facts(execution, "p" * 64)
        assert {f.field for f in facts} == set(EXECUTION_FIELDS)

    def test_a_fill_is_a_point_in_time_not_an_open_period(self) -> None:
        """`effective_to` is the trade date, not None. A fill happened at an
        instant; an open-ended period would claim it stays true."""
        execution = parse_execution(_filled_report())
        for fact in execution_facts(execution, "p" * 64):
            assert fact.effective_to == fact.effective_from == execution.transact_time.date()

    def test_all_facts_share_one_subject(self) -> None:
        execution = parse_execution(_filled_report())
        assert len({f.subject for f in execution_facts(execution, "p" * 64)}) == 1

    def test_knowledge_time_is_the_trade_time(self) -> None:
        """For our own execution there is no gap between the event and
        learning of it, unlike a filing where the two genuinely differ."""
        execution = parse_execution(_filled_report())
        for fact in execution_facts(execution, "p" * 64):
            assert fact.knowledge_from == execution.transact_time


class TestProvenance:
    def test_it_carries_the_archived_message_key(self) -> None:
        record = execution_provenance("a" * 64, received_at=NOW, source_uri="fix://SIM/CLIENT")
        assert record.payload_hash == "a" * 64

    def test_provenance_without_a_payload_is_refused(self) -> None:
        """An execution fact whose provenance names no payload is a number
        with a story attached. The archive exists so the story is checkable."""
        with pytest.raises(ValueError, match="needs the archived message"):
            execution_provenance("", received_at=NOW, source_uri="fix://SIM/CLIENT")

    def test_identical_reports_produce_identical_provenance_ids(self) -> None:
        """Provenance identity is a content hash (I5), so re-recording the
        same message does not fork the chain."""
        first = execution_provenance("a" * 64, received_at=NOW, source_uri="fix://x")
        second = execution_provenance("a" * 64, received_at=NOW, source_uri="fix://x")
        assert first.id == second.id


def test_every_fix_42_and_44_trade_code_is_accepted() -> None:
    """The session layer speaks 4.2 and 4.4. A version-dependent silence
    here would drop every fill from one of them."""
    assert {b"F", b"1", b"2"} == TRADE_EXEC_TYPES


def test_sell_short_signs_negative() -> None:
    for code, name in SIDES.items():
        execution = Execution(
            exec_id="E1",
            order_id="O1",
            symbol="IBM",
            side=name,
            last_qty=10.0,
            last_px=100.0,
            cum_qty=10.0,
            average_price=100.0,
            order_qty=10.0,
            transact_time=NOW,
        )
        expected = -10.0 if name.startswith("sell") else 10.0
        assert execution.signed_qty == expected, f"side {code!r} ({name}) signed wrongly"
