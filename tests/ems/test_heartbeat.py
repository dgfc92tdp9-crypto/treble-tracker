"""The session clock, at times that cannot be produced by waiting.

Every case here is reached by passing a datetime rather than by sleeping,
which is the point of the design. A test that waits three seconds proves
a heartbeat fires and says nothing about what happens when thirty
intervals pass between two polls — which is exactly when a naive
implementation emits thirty heartbeats, or none.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from treble.ems.heartbeat import MARGIN, HeartbeatMonitor, Liveness

START = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
INTERVAL = 30.0
#: One interval plus the transmission margin: when the peer's silence starts
#: counting against it.
SILENT = timedelta(seconds=INTERVAL * (1.0 + MARGIN))


def _monitor(interval: float = INTERVAL) -> HeartbeatMonitor:
    return HeartbeatMonitor(interval=interval, last_sent=START, last_received=START)


def at(seconds: float) -> datetime:
    return START + timedelta(seconds=seconds)


class TestAnIdleLink:
    def test_nothing_is_due_immediately(self) -> None:
        assert _monitor().due(START) is Liveness.IDLE

    def test_nothing_is_due_just_before_the_interval(self) -> None:
        assert _monitor().due(at(INTERVAL - 0.001)) is Liveness.IDLE

    def test_a_heartbeat_is_due_at_the_interval(self) -> None:
        assert _monitor().due(at(INTERVAL)) is Liveness.SEND_HEARTBEAT

    def test_sending_anything_defers_the_heartbeat(self) -> None:
        """A heartbeat is filler for an idle link. Sending one straight
        after an execution report tells the peer nothing it did not just
        learn, and doubles the traffic on a busy session."""
        monitor = _monitor()
        # The peer is heard from throughout, so the only timer in play is
        # the outbound one. Letting the receive timer expire here would
        # make the second assertion pass as SEND_TEST_REQUEST instead —
        # true, and true for the wrong reason.
        monitor.sent(at(INTERVAL - 1))
        monitor.received(at(INTERVAL - 1))
        assert monitor.due(at(INTERVAL)) is Liveness.IDLE
        monitor.received(at(2 * INTERVAL - 1))
        assert monitor.due(at(2 * INTERVAL - 1)) is Liveness.SEND_HEARTBEAT


class TestASilentPeer:
    def test_a_test_request_is_due_after_the_margin(self) -> None:
        monitor = _monitor()
        monitor.sent(at(INTERVAL * 10))  # keep the send timer quiet
        assert monitor.due(at(SILENT.total_seconds())) is Liveness.SEND_TEST_REQUEST

    def test_the_margin_is_not_free(self) -> None:
        """Without slack a session on a busy link test-requests
        continuously, so the margin has to actually delay the request."""
        monitor = _monitor()
        monitor.sent(at(INTERVAL * 10))
        assert monitor.due(at(INTERVAL)) is Liveness.IDLE
        assert monitor.due(at(SILENT.total_seconds() - 0.001)) is Liveness.IDLE

    def test_a_second_request_is_not_sent_while_one_is_outstanding(self) -> None:
        """A dead peer would otherwise get a flood — and worse, the
        disconnect deadline would restart with each one, so the session
        would never actually be dropped."""
        monitor = _monitor()
        monitor.sent(at(INTERVAL * 10))
        assert monitor.due(at(SILENT.total_seconds())) is Liveness.SEND_TEST_REQUEST
        monitor.test_request_sent("TR1", at(SILENT.total_seconds()))
        assert monitor.due(at(SILENT.total_seconds() + 1)) is Liveness.IDLE

    def test_an_unanswered_request_drops_the_session(self) -> None:
        monitor = _monitor()
        monitor.test_request_sent("TR1", at(100))
        assert monitor.due(at(100 + SILENT.total_seconds() - 0.001)) is not Liveness.DISCONNECT
        assert monitor.due(at(100 + SILENT.total_seconds())) is Liveness.DISCONNECT

    def test_any_inbound_message_answers_the_request(self) -> None:
        """Not only a heartbeat echoing the id. A peer that replied with
        an execution report is demonstrably alive, and dropping it for
        having been busy would be worse than the silence."""
        monitor = _monitor()
        monitor.test_request_sent("TR1", at(100))
        monitor.received(at(101))
        assert monitor.outstanding is None
        assert monitor.due(at(100 + SILENT.total_seconds())) is not Liveness.DISCONNECT


class TestUrgencyOrdering:
    def test_a_dead_session_is_dropped_rather_than_sent_a_heartbeat(self) -> None:
        """Both are due here. Writing filler down a connection already
        known to be dead is the wrong one to pick."""
        monitor = _monitor()
        monitor.test_request_sent("TR1", at(100))
        assert monitor.due(at(1000)) is Liveness.DISCONNECT

    def test_a_heartbeat_never_pre_empts_the_other_two(self) -> None:
        """The ordering that is actually load-bearing. Checking the send
        timer first would return SEND_HEARTBEAT for both a dead session
        and a silent peer, because the send timer expires first and stays
        expired."""
        dead = _monitor()
        dead.test_request_sent("TR1", at(100))
        assert dead.due(at(1000)) is Liveness.DISCONNECT
        silent = _monitor()
        assert silent.due(at(1000)) is Liveness.SEND_TEST_REQUEST

    def test_the_peer_is_asked_before_filler_is_sent(self) -> None:
        """Send and receive timers are both expired. The unanswered
        question is the more informative message."""
        monitor = _monitor()
        assert monitor.due(at(1000)) is Liveness.SEND_TEST_REQUEST


class TestHeartbeatsDisabled:
    """`HeartBtInt = 0` is a real setting, not a degenerate case."""

    @pytest.mark.parametrize("elapsed", [0, 1, 30, 10_000])
    def test_nothing_is_ever_due(self, elapsed: float) -> None:
        assert _monitor(interval=0.0).due(at(elapsed)) is Liveness.IDLE

    def test_not_even_a_disconnect(self) -> None:
        """An implementation that checked the disconnect deadline before
        the enabled flag would drop every session on a venue that runs
        without heartbeats."""
        monitor = _monitor(interval=0.0)
        monitor.test_request_sent("TR1", START)
        assert monitor.due(at(100_000)) is Liveness.IDLE

    def test_it_is_reported_as_disabled(self) -> None:
        assert not _monitor(interval=0.0).enabled
        assert _monitor().enabled


class TestALongGapBetweenPolls:
    def test_thirty_intervals_produce_one_heartbeat_not_thirty(self) -> None:
        """The case that cannot be produced by waiting: the process was
        stopped, or the loop starved, and time jumped. `due` returns one
        action, so the caller sends one heartbeat and asks again."""
        monitor = _monitor()
        monitor.sent(at(INTERVAL * 100))
        # Heard from at the moment of the poll: the peer is fine, this
        # side has simply not spoken for thirty intervals.
        monitor.received(at(INTERVAL * 130))
        assert monitor.due(at(INTERVAL * 130)) is Liveness.SEND_HEARTBEAT
        monitor.sent(at(INTERVAL * 130))
        assert monitor.due(at(INTERVAL * 130)) is Liveness.IDLE

    def test_a_clock_that_went_backwards_does_not_fire(self) -> None:
        """NTP steps clocks backwards. A negative elapsed time must read
        as 'no time has passed', not wrap into something due."""
        monitor = _monitor()
        assert monitor.due(at(-3600)) is Liveness.IDLE


class TestTheClockOverASocket:
    """One integration test, because the decision logic is covered above.

    What this adds is that the acceptor's clock task is actually wired to
    the writer — a `HeartbeatMonitor` that is exhaustively correct and
    never consulted would pass every test in this file.
    """

    @pytest.mark.asyncio
    async def test_an_idle_client_is_sent_a_heartbeat(self) -> None:
        import asyncio

        from treble.ems.session import MSG_TYPE, MsgType, Session
        from treble.ems.transport import HOST, read_messages, running_simulator

        # Short enough that the test does not wait, long enough that the
        # 0.25s poll gets a turn.
        async with running_simulator(heartbeat_seconds=0.3) as server:
            reader, writer = await asyncio.open_connection(HOST, server.port)
            client = Session(sender="TREBLE", target="SIM")
            writer.write(client.logon(now=datetime.now(UTC)))
            await writer.drain()

            seen: list[str] = []
            try:
                async with asyncio.timeout(5):
                    async for raw in read_messages(reader):
                        seen.append(_msg_type(raw))
                        if MsgType.HEARTBEAT.value in seen or MsgType.TEST_REQUEST.value in seen:
                            break
            finally:
                writer.close()

        assert MSG_TYPE  # the tag is what `_msg_type` reads
        assert seen[0] == MsgType.LOGON.value
        assert seen[-1] in (MsgType.HEARTBEAT.value, MsgType.TEST_REQUEST.value), (
            f"idle session produced only {seen}"
        )


def _msg_type(raw: bytes) -> str:
    import simplefix

    parser = simplefix.FixParser()
    parser.append_buffer(raw)
    message = parser.get_message()
    assert message is not None
    return (message.get(35) or b"").decode()
