"""The Matrix client, against a homeserver CI can run with no network.

Docker is not installed on the machine this was written on, so the real
Synapse path could not be exercised at all. Everything the client is
known to do, it is known to do against `im/simulator.py` — which is
therefore deliberately hostile on demand, because a client this author
wrote driven by a server this author wrote is otherwise a closed loop
that agrees with itself.

The two tests that matter are the two ways a chat client loses a
message: a sync token advanced past events it never handed over, and a
retry that posts twice.
"""

from __future__ import annotations

# ruff: noqa: S106 - the simulator's fixture credentials, never a real one
import pytest

from treble.im.matrix import MatrixClient, MatrixError
from treble.im.simulator import Homeserver

ROOM = "!desk:treble.invalid"


def _logged_in(**kwargs: object) -> tuple[MatrixClient, Homeserver]:
    server = Homeserver(**kwargs)  # type: ignore[arg-type]
    client = MatrixClient(transport=server.transport)
    client.login(user="jack", password="hunter2")
    client.join(ROOM)
    return client, server


class TestTheSession:
    def test_login_returns_the_servers_canonical_id(self) -> None:
        """Not what was typed. A homeserver may canonicalise a localpart,
        and the identity the directory records must be the one the server
        issued."""
        server = Homeserver(accounts={"Jack": "hunter2"})
        client = MatrixClient(transport=server.transport)
        assert client.login(user="Jack", password="hunter2") == "@jack:treble.invalid"

    def test_a_wrong_password_is_refused_with_the_servers_errcode(self) -> None:
        client = MatrixClient(transport=Homeserver().transport)
        with pytest.raises(MatrixError, match="M_FORBIDDEN") as caught:
            client.login(user="jack", password="wrong")
        assert caught.value.status == 403

    def test_whoami_asks_the_server_rather_than_memory(self) -> None:
        """A revoked token still has a user id sitting in memory. Reading
        it back would verify that we remember what we were told, which is
        not the same as the server still agreeing — and domain
        verification in `identity` depends on this round trip."""
        client, server = _logged_in()
        server.reject_token = True
        with pytest.raises(MatrixError, match="M_UNKNOWN_TOKEN"):
            client.whoami()
        assert client.user_id == "@jack:treble.invalid", "the stale id is still in memory"

    def test_an_unauthenticated_call_is_refused(self) -> None:
        client = MatrixClient(transport=Homeserver().transport)
        with pytest.raises(MatrixError, match="M_UNKNOWN_TOKEN"):
            client.joined_rooms()


class TestSendingIsIdempotent:
    def test_a_retry_under_the_same_key_does_not_double_post(self) -> None:
        """Matrix deduplicates on the transaction id, so a retry after an
        ambiguous timeout is safe *only* if the id is reused. Minting a
        fresh one is how one message becomes two — which in a chat that
        `VCON` turns into a booked trade is not cosmetic."""
        client, server = _logged_in()
        first = client.send(ROOM, "5mm at 98.25", key="axe-1")
        second = client.send(ROOM, "5mm at 98.25", key="axe-1")
        assert first == second
        assert len(server.rooms[ROOM]) == 1, "the message was posted twice"

    def test_two_unkeyed_sends_are_two_messages(self) -> None:
        """Typing the same thing twice means two messages."""
        client, server = _logged_in()
        assert client.send(ROOM, "ping") != client.send(ROOM, "ping")
        assert len(server.rooms[ROOM]) == 2

    def test_different_keys_are_different_messages(self) -> None:
        client, server = _logged_in()
        client.send(ROOM, "one", key="a")
        client.send(ROOM, "two", key="b")
        assert len(server.rooms[ROOM]) == 2


class TestSyncDoesNotLoseEvents:
    def test_the_token_advances_after_a_successful_batch(self) -> None:
        client, server = _logged_in()
        server.inject(ROOM, "@dealer:acme.com", "offered")
        assert client.since is None
        events = client.sync(timeout_ms=0)
        assert [e.body for e in events] == ["offered"]
        assert client.since is not None

    def test_a_second_sync_does_not_repeat_delivered_events(self) -> None:
        client, server = _logged_in()
        server.inject(ROOM, "@dealer:acme.com", "offered")
        client.sync(timeout_ms=0)
        assert client.sync(timeout_ms=0) == ()

    def test_the_token_is_unchanged_when_building_events_raises(self) -> None:
        """The property the ordering exists for. If the token advanced on
        receipt, a caller that raised while processing a batch would
        resume *after* those events and they would be gone — from the
        client's point of view they never arrived, and nothing reports a
        gap."""
        client, server = _logged_in()
        server.inject(ROOM, "@dealer:acme.com", "offered")
        client.sync(timeout_ms=0)
        good_token = client.since

        # An event the reducer cannot build: no event_id.
        server.inject_raw(
            ROOM,
            {
                "type": "m.room.message",
                "sender": "@x:y",
                "origin_server_ts": 0,
                "content": {"msgtype": "m.text", "body": "malformed"},
            },
        )
        with pytest.raises(KeyError):
            client.sync(timeout_ms=0)
        assert client.since == good_token, "the token advanced past a batch never delivered"

    def test_a_replayed_batch_is_tolerated(self) -> None:
        """A server may hand back a batch already seen after a network
        retry. Duplicated is survivable; skipped is not."""
        client, server = _logged_in()
        server.inject(ROOM, "@dealer:acme.com", "first")
        client.sync(timeout_ms=0)
        server.inject(ROOM, "@dealer:acme.com", "second")
        server.replay_next_sync = True
        bodies = [e.body for e in client.sync(timeout_ms=0)]
        assert "second" in bodies, "the new event was lost to the replay"


class TestOnlyChatLinesAreRendered:
    def test_a_membership_event_is_not_a_message(self) -> None:
        """Showing one as a chat line would put a null body into a
        transcript somebody may have to produce."""
        client, server = _logged_in()
        server.inject_raw(
            ROOM,
            {
                "event_id": "$m1",
                "type": "m.room.member",
                "sender": "@x:y",
                "origin_server_ts": 0,
                "content": {"membership": "join"},
            },
        )
        assert client.sync(timeout_ms=0) == ()

    def test_an_unexpected_type_carrying_text_content_is_not_rendered(self) -> None:
        """The membership test above does not isolate the *type* check —
        a membership event has no `msgtype`, so the msgtype filter catches
        it and deleting the type filter passed anyway.

        A peer controls the content of the events it sends, so an event
        of some other type can carry text-shaped content. Only the type
        check stops that reaching a transcript as a chat line.
        """
        client, server = _logged_in()
        server.inject_raw(
            ROOM,
            {
                "event_id": "$t1",
                "type": "m.room.topic",
                "sender": "@x:y",
                "origin_server_ts": 0,
                "content": {"msgtype": "m.text", "body": "not actually a message"},
            },
        )
        assert client.sync(timeout_ms=0) == ()

    def test_a_non_text_message_is_not_rendered(self) -> None:
        client, server = _logged_in()
        server.inject_raw(
            ROOM,
            {
                "event_id": "$i1",
                "type": "m.room.message",
                "sender": "@x:y",
                "origin_server_ts": 0,
                "content": {"msgtype": "m.image", "url": "mxc://x"},
            },
        )
        assert client.sync(timeout_ms=0) == ()

    def test_a_text_message_carries_its_sender_and_room(self) -> None:
        client, server = _logged_in()
        server.inject(ROOM, "@dealer:acme.com", "5mm at 98.25")
        (event,) = client.sync(timeout_ms=0)
        assert (event.sender, event.room_id, event.body) == (
            "@dealer:acme.com",
            ROOM,
            "5mm at 98.25",
        )
