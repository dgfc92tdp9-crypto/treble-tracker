"""The session clock: what to send when nothing is happening.

A FIX session that goes quiet is ambiguous. The counterparty may be idle,
or the TCP connection may have died in a way neither end has noticed —
a half-open socket accepts writes and delivers nothing, and the operating
system will not say so for minutes. Heartbeats exist to make silence
mean something.

The rules, from FIX 4.4 §Session Protocol:

* if nothing has been **sent** for one interval, send a Heartbeat;
* if nothing has been **received** for one interval plus a margin, send a
  TestRequest — "are you there *now*";
* if the TestRequest goes unanswered for the same period again, the
  session is dead and the connection must be dropped.

This is a pure decision function over an injected clock. It does not
sleep, own a socket, or start a task. Two reasons, and the second is the
important one:

1. a timer that sleeps can only be tested by waiting, so its tests are
   slow and its edge cases go untested;
2. **the failure this exists to catch cannot be reproduced by waiting.**
   A test that sleeps three seconds proves a heartbeat fires; it says
   nothing about what happens when 30 intervals elapse between two polls
   because the process was stopped, which is when a naive implementation
   emits thirty heartbeats or none.

So the caller polls :meth:`HeartbeatMonitor.due` with the time it has,
and every case — including the ones that never occur on loopback — is
reachable in a test by passing a different datetime.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

#: Fraction of the interval allowed on top before the peer's silence
#: counts. Transmission takes time and clocks disagree; without slack a
#: session on a busy link test-requests continuously. FIX says "a
#: reasonable transmission time" and does not fix a number — 20% is the
#: usual reading.
MARGIN = 0.2


class Liveness(enum.Enum):
    """What the session should do at this instant.

    One action, not a set. When several are due the most urgent is
    returned and the caller polls again, which keeps the caller from
    having to decide what "send a heartbeat *and* disconnect" means.
    """

    IDLE = "idle"
    SEND_HEARTBEAT = "send_heartbeat"
    SEND_TEST_REQUEST = "send_test_request"
    DISCONNECT = "disconnect"


@dataclass
class HeartbeatMonitor:
    """Tracks when each side last spoke, and what that implies.

    ``interval`` is the agreed HeartBtInt in seconds. **Zero disables
    heartbeats entirely** — a legitimate setting some venues use for
    sessions supervised another way — and is handled here rather than by
    every caller. It is not a degenerate case to be ignored: an
    implementation that treated 0 as "always due" would emit heartbeats
    in a tight loop, and one that divided by it would raise.
    """

    interval: float
    last_sent: datetime
    last_received: datetime
    #: The id of a TestRequest awaiting an answer, if one is outstanding.
    #: Held so a second is not sent every poll while the first is still
    #: pending — a dead peer would otherwise get a flood, and the
    #: disconnect deadline would keep resetting so the session would
    #: never actually be dropped.
    outstanding: str | None = None
    outstanding_at: datetime | None = None

    @property
    def enabled(self) -> bool:
        return self.interval > 0

    @property
    def _silence_allowed(self) -> timedelta:
        return timedelta(seconds=self.interval * (1.0 + MARGIN))

    def due(self, now: datetime) -> Liveness:
        """What to do now.

        The heartbeat is checked **last**, and that is the ordering that
        carries weight: a session that should be dropped, or a peer that
        should be challenged, must not first be sent filler down a
        connection that may already be dead.

        The relative order of the first two does *not* matter — verified
        by mutation, which is the only reason this says so. `outstanding
        is None` and `outstanding_at is not None` can never both hold, so
        swapping them is behaviour-preserving. Stated because the obvious
        reading is that disconnect-before-test-request is load-bearing,
        and a future reader would otherwise treat an equivalent mutant as
        a caught bug.
        """
        if not self.enabled:
            return Liveness.IDLE
        if self.outstanding_at is not None and now - self.outstanding_at >= self._silence_allowed:
            return Liveness.DISCONNECT
        if self.outstanding is None and now - self.last_received >= self._silence_allowed:
            return Liveness.SEND_TEST_REQUEST
        if now - self.last_sent >= timedelta(seconds=self.interval):
            return Liveness.SEND_HEARTBEAT
        return Liveness.IDLE

    def sent(self, now: datetime) -> None:
        """Record that *something* was sent.

        Every outbound message, not only heartbeats. A heartbeat is
        filler for an idle link; sending one straight after an execution
        report tells the peer nothing it did not just learn, and on a busy
        session doubles the message count for no information.
        """
        self.last_sent = now

    def received(self, now: datetime) -> None:
        """Record that *something* was received, and clear any test request.

        Any inbound message is evidence of life, so any inbound message
        answers the question a TestRequest asked. Requiring the echoed
        TestReqID specifically would drop a session that was demonstrably
        alive and merely busy — it had answered with an execution report
        instead of a heartbeat.
        """
        self.last_received = now
        self.outstanding = None
        self.outstanding_at = None

    def test_request_sent(self, request_id: str, now: datetime) -> None:
        """Record the TestRequest that :meth:`due` just asked for.

        Separate from :meth:`sent` because the caller must also record it
        as an outbound message; forgetting that would leave the send timer
        thinking the link had been silent and stack a heartbeat on top.
        """
        self.outstanding = request_id
        self.outstanding_at = now
        self.sent(now)


__all__ = ["MARGIN", "HeartbeatMonitor", "Liveness"]
