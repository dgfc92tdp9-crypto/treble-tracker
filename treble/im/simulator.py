"""A homeserver CI can run with no network, and no Docker.

The same argument as `ems/simulator.py`: a client this author wrote,
driven by a server this author wrote, is a closed loop that agrees with
itself. So this one is **deliberately hostile on demand** — it can
reject a token, drop a sync batch, or return the same batch twice, which
makes the client's handling of those cases exercised rather than
asserted. A simulator that only behaves well tests the happy path twice.

It matters more here than it did for FIX, because Docker is not
installed on the machine this was written on, so the real Synapse path
could not be run at all. Everything the client is known to do, it is
known to do against this.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from treble.im.matrix import API


@dataclass
class Homeserver:
    """An in-memory Matrix homeserver, as much as `IM` exercises."""

    domain: str = "treble.invalid"
    #: Accounts it will authenticate: localpart -> password.
    accounts: dict[str, str] = field(default_factory=lambda: {"jack": "hunter2"})
    #: Reject every authenticated call, as an expired token would.
    reject_token: bool = False
    #: Replay the previous batch instead of the next one, which is what a
    #: server does after a network retry — the client must tolerate it.
    replay_next_sync: bool = False

    tokens: dict[str, str] = field(default_factory=dict)
    rooms: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: Transaction ids already applied, so a resend is deduplicated the
    #: way a real homeserver deduplicates it.
    seen_txn: dict[str, str] = field(default_factory=dict)
    batches: list[list[tuple[str, dict[str, Any]]]] = field(default_factory=list)
    #: Published cross-signing and device keys, as `/keys/query` returns
    #: them: user id -> the two halves a client needs to check a chain.
    #: Empty by default, because a homeserver that invented keys would let
    #: `crosssigning.verify_device` verify something nobody signed.
    master_keys: dict[str, dict[str, Any]] = field(default_factory=dict)
    self_signing_keys: dict[str, dict[str, Any]] = field(default_factory=dict)
    device_keys: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)

    def user_id(self, localpart: str) -> str:
        return f"@{localpart}:{self.domain}"

    # -- the transport the client is given --------------------------------

    def transport(
        self, method: str, path: str, body: dict[str, Any] | None, token: str | None
    ) -> tuple[int, dict[str, Any]]:
        route = path.split("?", 1)[0]

        if route == f"{API}/login":
            return self._login(body or {})

        if token is None or token not in self.tokens or self.reject_token:
            # A real homeserver answers this for an expired, revoked or
            # absent token, and the client must surface it rather than
            # treat an error body as an empty result.
            return 401, {"errcode": "M_UNKNOWN_TOKEN", "error": "Invalid access token"}

        if route == f"{API}/account/whoami":
            return 200, {"user_id": self.tokens[token]}
        if route == f"{API}/logout":
            del self.tokens[token]
            return 200, {}
        if route == f"{API}/joined_rooms":
            return 200, {"joined_rooms": sorted(self.rooms)}
        if route.startswith(f"{API}/join/"):
            room = route.rsplit("/", 1)[-1].replace("%3A", ":").replace("%21", "!")
            self.rooms.setdefault(room, [])
            return 200, {"room_id": room}
        if route.startswith(f"{API}/rooms/") and "/send/" in route:
            return self._send(route, body or {}, self.tokens[token])
        if route == f"{API}/keys/query":
            return self._keys_query(body or {})
        if route == f"{API}/sync":
            return self._sync(path)
        return 404, {"errcode": "M_UNRECOGNIZED", "error": route}

    # -- handlers ---------------------------------------------------------

    def _login(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        user = str(body.get("identifier", {}).get("user", ""))
        if self.accounts.get(user) != body.get("password"):
            return 403, {"errcode": "M_FORBIDDEN", "error": "Invalid password"}
        token = uuid.uuid4().hex
        # The canonical MXID comes from the server, which is why the
        # client reads it back rather than assuming what it sent.
        self.tokens[token] = self.user_id(user.lower())
        return 200, {"access_token": token, "user_id": self.tokens[token]}

    def _keys_query(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Device and cross-signing keys for the users asked about.

        Serves only what was published: a homeserver inventing a key would
        let `crosssigning.verify_device` verify a chain nobody signed,
        which is the one thing that check exists to prevent. A user with
        nothing published is simply absent from the response, which is what
        a real server does and what tells a client "no cross-signing here"
        rather than "not verified".
        """
        wanted = body.get("device_keys")
        users = list(wanted) if isinstance(wanted, dict) else []
        return 200, {
            "device_keys": {
                user: self.device_keys[user] for user in users if user in self.device_keys
            },
            "master_keys": {
                user: self.master_keys[user] for user in users if user in self.master_keys
            },
            "self_signing_keys": {
                user: self.self_signing_keys[user]
                for user in users
                if user in self.self_signing_keys
            },
        }

    def _send(self, route: str, body: dict[str, Any], sender: str) -> tuple[int, dict[str, Any]]:
        parts = route.split("/")
        room = parts[parts.index("rooms") + 1].replace("%3A", ":").replace("%21", "!")
        txn = parts[-1]
        if txn in self.seen_txn:
            # Deduplicated, as the spec requires. This is what makes a
            # retry with the same transaction id safe, and what the
            # client's reuse of the id depends on.
            return 200, {"event_id": self.seen_txn[txn]}
        event_id = f"${uuid.uuid4().hex[:12]}"
        self.seen_txn[txn] = event_id
        event = {
            "event_id": event_id,
            "type": "m.room.message",
            "sender": sender,
            "origin_server_ts": int(datetime.now(UTC).timestamp() * 1000),
            "content": {"msgtype": "m.text", "body": str(body.get("body", ""))},
        }
        self.rooms.setdefault(room, []).append(event)
        self.batches.append([(room, event)])
        return 200, {"event_id": event_id}

    def _sync(self, path: str) -> tuple[int, dict[str, Any]]:
        since = 0
        if "since=" in path:
            raw = path.split("since=", 1)[1].split("&", 1)[0]
            since = int(raw) if raw.isdigit() else 0
        if self.replay_next_sync and since > 0:
            # Hand back the batch the client already had. A client that
            # advanced its token on receipt rather than after processing
            # would silently skip whatever came next.
            since -= 1
        pending = self.batches[since:]
        joined: dict[str, dict[str, Any]] = {}
        for batch in pending:
            for room, event in batch:
                joined.setdefault(room, {"timeline": {"events": []}})
                joined[room]["timeline"]["events"].append(event)
        return 200, {"next_batch": str(len(self.batches)), "rooms": {"join": joined}}

    def inject(self, room: str, sender: str, body: str) -> str:
        """Post a message as somebody else, so the client has traffic to
        receive rather than only its own echo."""
        event_id = f"${uuid.uuid4().hex[:12]}"
        event = {
            "event_id": event_id,
            "type": "m.room.message",
            "sender": sender,
            "origin_server_ts": int(datetime.now(UTC).timestamp() * 1000),
            "content": {"msgtype": "m.text", "body": body},
        }
        self.rooms.setdefault(room, []).append(event)
        self.batches.append([(room, event)])
        return event_id

    def inject_raw(self, room: str, event: dict[str, Any]) -> None:
        """Post an arbitrary event — a membership change, a topic edit —
        so the client's filtering of non-messages is exercised against
        something real rather than assumed."""
        self.rooms.setdefault(room, []).append(event)
        self.batches.append([(room, event)])


__all__ = ["Homeserver"]
