"""Reconnect: persisted counters and SequenceReset (P3_3).

A FIX session counts messages **per session, not per connection**. A process
that restarts and begins at 1 while the counterparty expects 47 is not
resuming a session — it is claiming forty-six messages never happened.

`SequenceReset` is the one door FIX leaves open for a counterparty to insist
on a counter, and the refusals here are the same decision as refusing to
absorb a gap, arriving through that door.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from treble.ems.session import SequenceResetError, Session
from treble.ems.simulator import Simulator, new_order_single
from treble.ems.store import VERSION, SessionStateError, resume, save, state_path

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def _traded(simulator: Simulator | None = None) -> Session:
    """A session that has logged on and done one trade."""
    client = Session(sender="TREBLE", target="SIM")
    sim = simulator or Simulator()
    for reply in sim.respond(client.logon(now=NOW), now=NOW):
        client.receive(reply)
    for reply in sim.respond(
        new_order_single(
            client, order_id="O1", symbol="IBM", side="1", quantity=100.0, price=98.5, now=NOW
        ),
        now=NOW,
    ):
        client.receive(reply)
    return client


class TestCountersSurviveARestart:
    def test_both_counters_come_back(self, tmp_path: Path) -> None:
        before = _traded()
        save(before, tmp_path)
        after = resume(tmp_path, sender="TREBLE", target="SIM")
        assert (after.outbound_seq, after.inbound_seq) == (
            before.outbound_seq,
            before.inbound_seq,
        )

    def test_a_missing_file_is_a_fresh_session_not_an_error(self, tmp_path: Path) -> None:
        """A first connection has nothing to resume, and refusing it would
        make the happy path require a file that cannot exist yet."""
        fresh = resume(tmp_path, sender="TREBLE", target="SIM")
        assert (fresh.outbound_seq, fresh.inbound_seq) == (1, 1)

    def test_logon_is_not_restored(self, tmp_path: Path) -> None:
        """A session resumes its counters, never its authentication. The
        connection is gone; treating a remembered logon as a live one would
        let a business message through before the peer identified itself on
        *this* connection."""
        traded = _traded()
        assert traded.logged_on
        save(traded, tmp_path)
        assert resume(tmp_path, sender="TREBLE", target="SIM").logged_on is False

    def test_an_interrupted_save_leaves_the_previous_state_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The property atomicity actually buys, asserted by interrupting it.

        The first version of this test checked only that no `.partial` file
        was left behind — which is equally true of a plain `write_text`, so
        replacing the atomic save with a direct one passed all fifteen tests.
        That is the same unfailable check `render/layout.py` had to fix, made
        again here by the same author who wrote the note about it.

        So the rename is interrupted, and what matters is that the file on
        disk still holds the *old* counters: a session resuming from a
        half-written state looks resumable and is not, which is worse than no
        file at all because no file starts fresh and says so.
        """
        early = Session(sender="TREBLE", target="SIM", outbound_seq=5, inbound_seq=5)
        save(early, tmp_path)

        def _fail(self: Path, target: object) -> None:
            raise OSError("interrupted mid-rename")

        monkeypatch.setattr(Path, "replace", _fail)
        with pytest.raises(OSError, match="interrupted"):
            save(Session(sender="TREBLE", target="SIM", outbound_seq=99, inbound_seq=99), tmp_path)

        monkeypatch.undo()
        survived = resume(tmp_path, sender="TREBLE", target="SIM")
        assert survived.outbound_seq == 5, "the interrupted save corrupted the old state"

    def test_no_partial_file_is_left_behind_on_success(self, tmp_path: Path) -> None:
        """Weaker than the test above and kept for what it does cover: a
        successful save must not leave litter that a later resume might
        read."""
        save(_traded(), tmp_path)
        assert not list(tmp_path.glob("*.partial"))
        assert state_path(tmp_path, sender="TREBLE", target="SIM").exists()

    def test_two_sessions_do_not_share_a_file(self, tmp_path: Path) -> None:
        """One shared file would make two sessions overwrite each other's
        counters, and the symptom would be a sequence error on whichever
        reconnected second."""
        first = Session(sender="TREBLE", target="SIM", outbound_seq=9, inbound_seq=9)
        second = Session(sender="TREBLE", target="OTHER", outbound_seq=2, inbound_seq=2)
        save(first, tmp_path)
        save(second, tmp_path)
        assert resume(tmp_path, sender="TREBLE", target="SIM").outbound_seq == 9
        assert resume(tmp_path, sender="TREBLE", target="OTHER").outbound_seq == 2


class TestUntrustworthyStateIsRefused:
    def test_a_truncated_file_is_refused(self, tmp_path: Path) -> None:
        state_path(tmp_path, sender="TREBLE", target="SIM").write_text('{"version": 1, "outb')
        with pytest.raises(SessionStateError, match="not readable JSON"):
            resume(tmp_path, sender="TREBLE", target="SIM")

    def test_a_future_version_is_refused(self, tmp_path: Path) -> None:
        state_path(tmp_path, sender="TREBLE", target="SIM").write_text(
            json.dumps({"version": VERSION + 1, "sender": "TREBLE", "target": "SIM"})
        )
        with pytest.raises(SessionStateError, match="version"):
            resume(tmp_path, sender="TREBLE", target="SIM")

    def test_a_file_recording_another_pair_is_refused(self, tmp_path: Path) -> None:
        path = state_path(tmp_path, sender="TREBLE", target="SIM")
        path.write_text(
            json.dumps(
                {
                    "version": VERSION,
                    "sender": "SOMEONE",
                    "target": "ELSE",
                    "outbound_seq": 5,
                    "inbound_seq": 5,
                }
            )
        )
        with pytest.raises(SessionStateError, match="records"):
            resume(tmp_path, sender="TREBLE", target="SIM")


class TestSequenceReset:
    """The one door FIX leaves open for a peer to insist on a counter."""

    @staticmethod
    def _peer_at(client: Session) -> Session:
        peer = Session(sender="SIM", target="TREBLE")
        peer.outbound_seq = client.inbound_seq
        return peer

    def test_moving_the_counter_backwards_is_refused(self) -> None:
        """The next message would duplicate one already processed, and a fill
        counted twice is a position nobody holds."""
        client = _traded()
        peer = self._peer_at(client)
        with pytest.raises(SequenceResetError, match="back to 1"):
            client.receive(peer.sequence_reset(new_seq_no=1, gap_fill=False, now=NOW))

    def test_a_backwards_reset_leaves_the_counter_alone(self) -> None:
        client = _traded()
        before = client.inbound_seq
        peer = self._peer_at(client)
        with pytest.raises(SequenceResetError):
            client.receive(peer.sequence_reset(new_seq_no=1, gap_fill=False, now=NOW))
        assert client.inbound_seq == before

    def test_a_forward_reset_is_legal_and_counts_what_it_discarded(self) -> None:
        """Forward is lossy but legal. Counted rather than absorbed: a
        session that lost eleven messages to an administrative reset and
        reported nothing looks identical to one that lost none."""
        client = _traded()
        peer = self._peer_at(client)
        target = client.inbound_seq + 11
        client.receive(peer.sequence_reset(new_seq_no=target, gap_fill=False, now=NOW))
        assert client.inbound_seq == target
        assert client.discarded == 11

    def test_a_gap_fill_loses_nothing(self) -> None:
        """GapFill is a placeholder for administrative messages not worth
        resending. It consumes its numbers and discards no business data, so
        it must not be counted as loss."""
        client = _traded()
        peer = self._peer_at(client)
        client.receive(
            peer.sequence_reset(new_seq_no=client.inbound_seq + 4, gap_fill=True, now=NOW)
        )
        assert client.discarded == 0

    def test_a_reset_with_no_new_sequence_number_is_refused(self) -> None:
        """Without NewSeqNo the message says "reset" and not to what."""
        client = _traded()
        peer = self._peer_at(client)
        raw = peer.encode(
            __import__("treble.ems.session", fromlist=["MsgType"]).MsgType.SEQUENCE_RESET,
            (),
            now=NOW,
        )
        with pytest.raises(SequenceResetError, match="no NewSeqNo"):
            client.receive(raw)

    def test_a_reset_to_the_same_number_is_a_no_op(self) -> None:
        client = _traded()
        peer = self._peer_at(client)
        before = client.inbound_seq
        client.receive(peer.sequence_reset(new_seq_no=before, gap_fill=False, now=NOW))
        assert client.inbound_seq == before
        assert client.discarded == 0


class TestTheWholeReconnect:
    def test_a_session_resumes_and_keeps_trading(self, tmp_path: Path) -> None:
        """The property all of the above exists for: the numbers continue
        across a restart, so the peer's next message is the one this side
        expects."""
        simulator = Simulator()
        first = _traded(simulator)
        save(first, tmp_path)

        revived = resume(tmp_path, sender="TREBLE", target="SIM")
        # A reconnect re-authenticates on the new connection.
        for reply in simulator.respond(revived.logon(now=NOW), now=NOW):
            revived.receive(reply)
        assert revived.logged_on
        for reply in simulator.respond(
            new_order_single(
                revived,
                order_id="O2",
                symbol="IBM",
                side="1",
                quantity=100.0,
                price=98.5,
                now=NOW,
            ),
            now=NOW,
        ):
            report = revived.receive(reply)
        assert report.get(11).decode() == "O2"
        assert revived.inbound_seq > first.inbound_seq
