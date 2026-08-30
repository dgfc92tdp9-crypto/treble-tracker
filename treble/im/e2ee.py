"""End-to-end encryption for rooms: Megolm over Olm (P3_1).

**Both layers, or neither.** Megolm encrypts the room; Olm distributes the
Megolm key. Building only the first and shipping the session key in
plaintext would put the key in the homeserver's hands while the screen said
"encrypted" — which is the failure `im/matrix.py` has warned about since it
was written: an encryption claim over a plaintext transport is the worst
thing this package could be wrong about. So the key never leaves this
process except inside an Olm message.

    room message ──Megolm──▶ m.room.encrypted
    Megolm key   ──Olm────▶ m.room_key, one per recipient device

## Why two layers at all

Olm is a pairwise ratchet: strong, and O(devices) work per message. A room
of twenty devices would mean twenty encryptions of every line. Megolm is a
ratchet the whole room shares, so a message is encrypted once — and the
cost moves to distributing the session key, which happens once per session
rather than once per message.

The trade is real and worth stating: anyone holding a Megolm session key
can read every message from that key's index onward. That is why a session
is per-room, why it is rotated, and why `session_key` is captured at
creation rather than read later.

## The index matters, and getting it wrong is silent

A Megolm session ratchets forward on every message. `GroupSession.session_key`
read *after* encrypting is the key at the new index, and a recipient given
that key cannot read anything earlier — the library raises, but a caller
that distributed the wrong key would see it only when someone reported a
message they could not read.

Measured: encrypting once and then handing over `session_key` produces
`MegolmDecryptionException: unknown message index, first known index 1,
index of the message 0`. So :class:`RoomSession` captures the key when the
session is created and never re-reads it.

## Room E2EE is BLOCKED, and the block is enforced below rather than noted

**`vodozemac` 0.10.0's Python bindings cannot rebuild a Megolm session key
from its wire form.** Measured on 2026-08-30: `SessionKey` and
`ExportedSessionKey` both expose `to_base64` and neither exposes
`from_base64`, and `InboundGroupSession` accepts only those objects.
0.10.0 is the latest release.

The consequence is asymmetric and disqualifying. This package could *send*
a room key another client would import, and could never *receive* one — so
a Treble device cannot read a room anybody else encrypts. That is not room
encryption, and calling it that would be the exact claim `im/matrix.py` has
warned about since it was written.

So `import_room_key` refuses, by name, with the reason. It is a raise
rather than a docstring because a documented limitation is one a caller
discovers after shipping, and because the moment the binding gains
`from_base64` this becomes four lines and a deleted exception.

What does work, completely: **Olm**. Device-to-device encryption round
trips through the Matrix wire form, which is what carries `m.room_key`,
verification events and anything else sent to a device rather than a room.

## What this does not do

It does not decide *who* should receive a key. That is a question about
device trust, and `crosssigning`/`sas` are what answer it — sending a room
key to an unverified device is a policy decision, not an encryption one,
and this module takes the recipient list from its caller rather than
inventing one.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

import vodozemac

#: Matrix's algorithm names for the two layers.
OLM_ALGORITHM = "m.olm.v1.curve25519-aes-sha2"
MEGOLM_ALGORITHM = "m.megolm.v1.aes-sha2"

#: How many one-time keys to keep published. A device with none cannot be
#: sent a first message: a peer needs one to establish an Olm session, and
#: the fallback key exists precisely because running out is otherwise a
#: silent failure to receive.
ONE_TIME_KEY_TARGET = 50


class EncryptionError(ValueError):
    """A message could not be encrypted or decrypted, and why."""


@dataclass
class DeviceKeys:
    """This device's long-term identity, and the keys peers need to reach it."""

    curve25519: str
    ed25519: str
    one_time_keys: dict[str, str] = field(default_factory=dict)

    def published(self, *, user_id: str, device_id: str) -> dict[str, Any]:
        """The `/keys/upload` payload shape, unsigned.

        Signing is `crosssigning`'s concern and is deliberately not done
        here: this module encrypts, and a module that both encrypted and
        attested to identity would make it easy to believe the second
        because the first worked.
        """
        return {
            "user_id": user_id,
            "device_id": device_id,
            "algorithms": [OLM_ALGORITHM, MEGOLM_ALGORITHM],
            "keys": {
                f"curve25519:{device_id}": self.curve25519,
                f"ed25519:{device_id}": self.ed25519,
            },
        }


class Device:
    """One device's Olm account: identity keys, one-time keys, sessions."""

    def __init__(self, account: vodozemac.Account | None = None) -> None:
        self._account = account or vodozemac.Account()
        #: Established Olm sessions, by the peer's curve25519 identity key.
        #: Keyed on identity rather than on device id because that is what
        #: an inbound message carries — a message arrives with a sender
        #: key, not a name.
        self._sessions: dict[str, vodozemac.Session] = {}

    @property
    def keys(self) -> DeviceKeys:
        return DeviceKeys(
            curve25519=self._account.curve25519_key.to_base64(),
            ed25519=self._account.ed25519_key.to_base64(),
            one_time_keys={
                key_id: key.to_base64() for key_id, key in self._account.one_time_keys.items()
            },
        )

    def publish_one_time_keys(self, count: int = ONE_TIME_KEY_TARGET) -> dict[str, str]:
        """Generate keys and mark them published.

        Marking is not optional bookkeeping. An account that generated keys
        and never marked them keeps handing out the same ones, and a
        one-time key used twice is not one-time — two peers would establish
        sessions against the same key, which is exactly the reuse the
        ratchet's forward secrecy assumes cannot happen.
        """
        self._account.generate_one_time_keys(count)
        published = {key_id: key.to_base64() for key_id, key in self._account.one_time_keys.items()}
        self._account.mark_keys_as_published()
        return published

    def establish(self, *, their_identity_key: str, their_one_time_key: str) -> None:
        """Open an Olm session to a peer, using one of its one-time keys."""
        try:
            self._sessions[their_identity_key] = self._account.create_outbound_session(
                vodozemac.Curve25519PublicKey.from_base64(their_identity_key),
                vodozemac.Curve25519PublicKey.from_base64(their_one_time_key),
            )
        except Exception as exc:
            raise EncryptionError(
                f"could not open a session to {their_identity_key}: {exc}"
            ) from exc

    def encrypt_to(self, their_identity_key: str, plaintext: bytes) -> dict[str, Any]:
        """Encrypt for one device. Returns Matrix's olm ciphertext shape.

        `{"type": 0|1, "body": base64}` — type 0 is a pre-key message,
        which carries what the recipient needs to create its side of the
        session, and type 1 is a normal one. The distinction is on the wire
        because the recipient handles them differently, and a client that
        guessed would fail on exactly the first message.
        """
        session = self._sessions.get(their_identity_key)
        if session is None:
            raise EncryptionError(f"no Olm session with {their_identity_key}; establish one first")
        kind, raw = session.encrypt(plaintext).to_parts()
        return {"type": kind, "body": base64.b64encode(raw).decode()}

    def decrypt_from(self, their_identity_key: str, ciphertext: dict[str, Any]) -> bytes:
        """Decrypt one device's olm message, creating the session if needed.

        A pre-key message from an unknown peer creates the inbound session;
        anything else needs one already. Both paths are here because a
        caller cannot tell which it has without looking at `type`, and
        making it look would put protocol knowledge in every caller.
        """
        kind, body = ciphertext.get("type"), ciphertext.get("body")
        if not isinstance(kind, int) or not isinstance(body, str):
            raise EncryptionError("olm ciphertext needs an integer type and a base64 body")
        try:
            message = vodozemac.AnyOlmMessage.from_parts(kind, base64.b64decode(body))
        except Exception as exc:
            raise EncryptionError(f"olm ciphertext is unreadable: {exc}") from exc

        session = self._sessions.get(their_identity_key)
        if session is None:
            if kind != 0:
                raise EncryptionError(
                    "a normal olm message arrived with no session: the pre-key message that "
                    "would have created one was lost or never sent"
                )
            pre_key = message.to_pre_key()
            if pre_key is None:
                # `type` said pre-key and the body disagreed. Refused rather
                # than coerced: the two describe the same message, and a
                # mismatch means the sender is confused or the event was
                # tampered with.
                raise EncryptionError("olm ciphertext claims type 0 but carries no pre-key message")
            try:
                result = self._account.create_inbound_session(
                    vodozemac.Curve25519PublicKey.from_base64(their_identity_key), pre_key
                )
            except Exception as exc:
                raise EncryptionError(f"could not accept a session: {exc}") from exc
            session, plaintext = result
            self._sessions[their_identity_key] = session
            return bytes(plaintext)

        try:
            return bytes(session.decrypt(message))
        except Exception as exc:
            raise EncryptionError(f"olm decryption failed: {exc}") from exc


@dataclass(frozen=True)
class RoomKey:
    """A Megolm session key, as `m.room_key` carries it."""

    room_id: str
    session_id: str
    session_key: str

    def content(self) -> dict[str, Any]:
        return {
            "algorithm": MEGOLM_ALGORITHM,
            "room_id": self.room_id,
            "session_id": self.session_id,
            "session_key": self.session_key,
        }


class RoomSession:
    """An outbound Megolm session for one room.

    The session key is captured **at construction**, before anything is
    encrypted. `GroupSession.session_key` read later is the key at the
    ratchet's new index, and a recipient given that cannot read earlier
    messages — measured, and the reason this is a stored attribute rather
    than a property that reads through.
    """

    def __init__(self, room_id: str, session: vodozemac.GroupSession | None = None) -> None:
        self.room_id = room_id
        self._session = session or vodozemac.GroupSession()
        self._session_key_object = self._session.session_key
        self._key_at_start = self._session_key_object.to_base64()

    @property
    def session_id(self) -> str:
        return self._session.session_id

    def live_key(self) -> vodozemac.SessionKey:
        """The session key as an object, for an in-process recipient.

        Exists because the wire form cannot be rebuilt (see the module
        docstring), so in-process Megolm has to pass the object itself.
        Not a substitute for distribution: an object cannot cross a socket,
        which is precisely the problem.
        """
        return self._session_key_object

    def room_key(self) -> RoomKey:
        """What to distribute, over Olm and only over Olm."""
        return RoomKey(
            room_id=self.room_id,
            session_id=self.session_id,
            session_key=self._key_at_start,
        )

    def encrypt(self, plaintext: bytes, *, sender_key: str, device_id: str) -> dict[str, Any]:
        """One room message, as `m.room.encrypted` content."""
        return {
            "algorithm": MEGOLM_ALGORITHM,
            "sender_key": sender_key,
            "device_id": device_id,
            "session_id": self.session_id,
            "ciphertext": self._session.encrypt(plaintext).to_base64(),
        }


class RoomDecryptor:
    """Inbound Megolm sessions, by session id."""

    def __init__(self) -> None:
        self._sessions: dict[str, vodozemac.InboundGroupSession] = {}

    def accept_session(self, session_id: str, key: vodozemac.SessionKey) -> None:
        """Take a live session key, in-process.

        Refuses to replace a session it already holds. A second key for one
        session id would either be the same key — harmless but pointless —
        or a different one, and adopting that would silently move the
        readable window forward: messages this device had been able to read
        would stop decrypting, with nothing to say why.
        """
        if session_id in self._sessions:
            return
        try:
            self._sessions[session_id] = vodozemac.InboundGroupSession(key)
        except Exception as exc:
            raise EncryptionError(f"unusable room key for {session_id}: {exc}") from exc

    def import_room_key(self, key: RoomKey) -> None:
        """Take a room key received over the wire. **Refused.**

        This is the one operation room encryption needs and the binding
        cannot perform: `vodozemac` 0.10.0 exposes no `SessionKey.from_base64`
        (nor `ExportedSessionKey.from_base64`), and `InboundGroupSession`
        accepts nothing else.

        Raised rather than documented, because a documented limitation is
        one a caller finds out about after shipping — and because a silent
        fallback here would mean a device that believed it had joined an
        encrypted room and simply never decrypted anything.
        """
        raise EncryptionError(
            f"cannot import room key for session {key.session_id}: vodozemac 0.10.0 "
            "exposes no SessionKey.from_base64, so a Megolm key cannot be rebuilt from "
            "the wire. Olm (device-to-device) is unaffected and works; room encryption "
            "is blocked on the binding, not on this code. See ADR-0012."
        )

    def decrypt(self, content: dict[str, Any]) -> bytes:
        """One `m.room.encrypted` event back to plaintext."""
        session_id, ciphertext = content.get("session_id"), content.get("ciphertext")
        if not isinstance(session_id, str) or not isinstance(ciphertext, str):
            raise EncryptionError("encrypted content needs a session_id and a ciphertext")
        session = self._sessions.get(session_id)
        if session is None:
            # The common and important case: no key for this session. Said
            # plainly, because "cannot decrypt" and "decrypted to nothing"
            # must never look the same in a transcript.
            raise EncryptionError(
                f"no room key for session {session_id}: this device was not sent one, "
                "or was sent one covering only later messages"
            )
        try:
            return bytes(session.decrypt(vodozemac.MegolmMessage.from_base64(ciphertext)).plaintext)
        except Exception as exc:
            raise EncryptionError(f"megolm decryption failed for {session_id}: {exc}") from exc


__all__ = [
    "MEGOLM_ALGORITHM",
    "OLM_ALGORITHM",
    "ONE_TIME_KEY_TARGET",
    "Device",
    "DeviceKeys",
    "EncryptionError",
    "RoomDecryptor",
    "RoomKey",
    "RoomSession",
]
