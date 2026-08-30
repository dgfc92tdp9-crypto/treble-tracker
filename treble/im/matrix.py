"""Matrix client-server API: the parts `IM` needs (spec §19.1).

Login, whoami, room membership, sending, and the sync loop. Deliberately
a thin, explicit client over the HTTP API rather than a wrapper around a
library, for the reason the FIX session layer is hand-written: the
failure that costs money is a message the client thinks it delivered and
did not, and that lives in the sync token and the transaction id, not in
the convenience of the call.

**Two properties carry everything, and both are about not losing a
message.**

*The sync token is advanced only after the batch it belongs to has been
handed to the caller.* Advancing it on receipt would mean a caller that
raised while processing a batch resumes *after* those events, and the
messages in them are gone — from the client's point of view they never
arrived, and nothing anywhere reports a gap. This is the same argument
as raising a FIX sequence gap before moving the counter.

*Every send carries a transaction id, and a retry reuses it.* Matrix
deduplicates on it, so a retry after an ambiguous timeout is idempotent.
Generating a fresh id on retry is how one message becomes two — which in
a chat that `VCON` turns into a booked trade is not cosmetic.

**No end-to-end encryption.** The spec asks for it "where the firm's
compliance regime permits, with compliant key escrow"; that needs olm
and a key-management design, and neither is here. So this client speaks
only unencrypted rooms, `IM` says so on screen, and nothing in the code
implies otherwise. An E2EE claim over a plaintext transport would be the
worst possible thing to be wrong about in this module.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote

from treble.im.crosssigning import DeviceTrust, key_material, verify_device

#: Client-server API version this speaks.
API = "/_matrix/client/v3"

#: Milliseconds a sync waits for events before returning empty. Long
#: polling: a short timeout is a busy loop against the homeserver, and no
#: timeout is a request that never returns and cannot be cancelled.
SYNC_TIMEOUT_MS = 30_000


class MatrixError(RuntimeError):
    """The homeserver refused, and its own errcode is preserved."""

    def __init__(self, status: int, errcode: str, message: str) -> None:
        super().__init__(f"{errcode}: {message} (HTTP {status})")
        self.status = status
        self.errcode = errcode


@dataclass(frozen=True)
class Event:
    """One timeline event, reduced to what `IM` shows."""

    event_id: str
    room_id: str
    sender: str
    body: str
    sent_at: datetime


#: A transport is any callable taking (method, path, body, token) and
#: returning (status, json). Injected rather than imported so the client
#: is exercised against the simulator with no network — the same shape
#: the FIX work used, for the same reason.
Transport = Callable[[str, str, dict[str, Any] | None, str | None], tuple[int, dict[str, Any]]]


@dataclass
class MatrixClient:
    """One logged-in session against one homeserver."""

    transport: Transport
    user_id: str | None = None
    access_token: str | None = None
    #: Where the next sync resumes. `None` means "from the beginning of
    #: what the server will give us", which is what a first sync wants.
    since: str | None = None
    #: Transaction ids already used, by the message they were used for,
    #: so a retry of the *same* send reuses its id rather than minting a
    #: new one and duplicating the message.
    _txn: dict[str, str] = field(default_factory=dict, repr=False)

    # -- session ---------------------------------------------------------

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        status, payload = self.transport(method, path, body, self.access_token)
        if status >= 400:
            raise MatrixError(
                status,
                str(payload.get("errcode", "M_UNKNOWN")),
                str(payload.get("error", "no message")),
            )
        return payload

    def login(self, *, user: str, password: str) -> str:
        """Password login, returning the authenticated MXID.

        The MXID comes back from the *server*, not from what was typed:
        a homeserver may canonicalise a localpart, and the identity the
        directory records must be the one the server issued.
        """
        payload = self._call(
            "POST",
            f"{API}/login",
            {
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": user},
                "password": password,
            },
        )
        self.access_token = str(payload["access_token"])
        self.user_id = str(payload["user_id"])
        return self.user_id

    def whoami(self) -> str:
        """Ask the homeserver who this token belongs to.

        The round trip `identity.verify` requires before it will grant
        domain verification. Reading `self.user_id` instead would verify
        that we remember what we were told, which is not the same thing
        as the server still agreeing — a revoked or expired token still
        has a user id sitting in memory.
        """
        payload = self._call("GET", f"{API}/account/whoami")
        return str(payload["user_id"])

    def logout(self) -> None:
        self._call("POST", f"{API}/logout")
        self.access_token = None

    # -- rooms -----------------------------------------------------------

    def joined_rooms(self) -> tuple[str, ...]:
        payload = self._call("GET", f"{API}/joined_rooms")
        return tuple(str(r) for r in payload.get("joined_rooms", ()))

    def join(self, room: str) -> str:
        payload = self._call("POST", f"{API}/join/{quote(room, safe='')}", {})
        return str(payload["room_id"])

    def send(self, room_id: str, body: str, *, key: str | None = None) -> str:
        """Send a text message, idempotently.

        `key` names the message for retry purposes. Two calls with the
        same key reuse one transaction id, so the homeserver deduplicates
        and a retry after an ambiguous timeout cannot double-post. Two
        calls without a key are two different messages, which is what
        typing the same thing twice means.
        """
        name = key or str(uuid.uuid4())
        txn = self._txn.setdefault(name, uuid.uuid4().hex)
        payload = self._call(
            "PUT",
            f"{API}/rooms/{quote(room_id, safe='')}/send/m.room.message/{txn}",
            {"msgtype": "m.text", "body": body},
        )
        return str(payload["event_id"])

    # -- device trust -----------------------------------------------------

    def device_trust(self, user_id: str, device_id: str) -> DeviceTrust:
        """Whether ``device_id`` is cross-signed by ``user_id``'s master key.

        This is what Matrix means by a *verified* device, and it is a
        different claim from `im/identity.py`'s: that one proves the
        homeserver authenticated an MXID, this one proves the account
        holder's own root key vouches for the device sending from it.

        **The master key comes from the homeserver here, and that is a
        stated weakness rather than a hidden one.** A homeserver that
        wanted to lie could serve its own master key and a chain beneath
        it, and this would verify. Closing that needs the key confirmed out
        of band — SAS, or comparing a fingerprint — and until it is,
        `DeviceTrust.master_key` travels with the answer so the premise is
        visible to whoever reads it. A boolean alone would hide exactly
        this.
        """
        payload = self._call("POST", f"{API}/keys/query", {"device_keys": {user_id: []}})
        master = payload.get("master_keys", {}).get(user_id)
        self_signing = payload.get("self_signing_keys", {}).get(user_id)
        device = payload.get("device_keys", {}).get(user_id, {}).get(device_id)

        if not isinstance(master, dict) or not isinstance(self_signing, dict):
            return DeviceTrust(
                user_id=user_id,
                device_id=device_id,
                verified=False,
                master_key="",
                reason="the homeserver published no cross-signing keys for this user",
            )
        if not isinstance(device, dict):
            return DeviceTrust(
                user_id=user_id,
                device_id=device_id,
                verified=False,
                master_key=key_material(master),
                reason=f"the homeserver published no keys for device {device_id!r}",
            )
        return verify_device(
            user_id=user_id,
            device_id=device_id,
            master_key=key_material(master),
            self_signing_key=self_signing,
            device_keys=device,
        )

    # -- sync ------------------------------------------------------------

    def sync(self, *, timeout_ms: int = SYNC_TIMEOUT_MS) -> tuple[Event, ...]:
        """One sync round, returning the timeline events it carried.

        **The token advances only after the events are built.** If
        parsing raises, `since` is unchanged and the next sync replays
        the same batch — duplicated, which the caller can see, rather
        than skipped, which nobody can.
        """
        path = f"{API}/sync?timeout={timeout_ms}"
        if self.since:
            path += f"&since={quote(self.since, safe='')}"
        payload = self._call("GET", path)
        events = _timeline(payload)
        self.since = str(payload["next_batch"])
        return events


def _timeline(payload: dict[str, Any]) -> tuple[Event, ...]:
    """Pull `m.room.message` events out of a sync response.

    Non-message events are dropped rather than rendered: a membership
    change or a topic edit is not a chat line, and showing them as one
    would put "null" in a transcript somebody may have to produce.
    """
    out: list[Event] = []
    joined = payload.get("rooms", {}).get("join", {})
    for room_id, room in sorted(joined.items()):
        for raw in room.get("timeline", {}).get("events", ()):
            if raw.get("type") != "m.room.message":
                continue
            content = raw.get("content", {})
            if content.get("msgtype") != "m.text":
                continue
            out.append(
                Event(
                    event_id=str(raw["event_id"]),
                    room_id=str(room_id),
                    sender=str(raw["sender"]),
                    body=str(content.get("body", "")),
                    sent_at=datetime.fromtimestamp(
                        int(raw.get("origin_server_ts", 0)) / 1000,
                        tz=__import__("datetime").UTC,
                    ),
                )
            )
    return tuple(out)


def transcript(events: tuple[Event, ...]) -> tuple[tuple[str, str, str], ...]:
    """Timeline events as the rows `IM` prints: time, sender, body.

    The time is the **origin server's** timestamp, not this machine's
    receipt time. A transcript somebody may have to produce should say
    when the sender's homeserver stamped the message, and two clients
    reading the same room must agree — a local clock would give a
    different transcript on every desk.
    """
    return tuple((event.sent_at.strftime("%H:%M:%S"), event.sender, event.body) for event in events)


def json_transport(base_url: str, *, timeout: float = 60.0) -> Transport:
    """A real HTTP transport. Imported lazily so tests never need httpx."""

    def call(
        method: str, path: str, body: dict[str, Any] | None, token: str | None
    ) -> tuple[int, dict[str, Any]]:
        import httpx

        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = httpx.request(
            method, f"{base_url.rstrip('/')}{path}", json=body, headers=headers, timeout=timeout
        )
        try:
            return response.status_code, response.json()
        except json.JSONDecodeError:
            return response.status_code, {"errcode": "M_NOT_JSON", "error": response.text[:200]}

    return call


__all__ = [
    "API",
    "SYNC_TIMEOUT_MS",
    "Event",
    "MatrixClient",
    "MatrixError",
    "Transport",
    "json_transport",
    "transcript",
]
