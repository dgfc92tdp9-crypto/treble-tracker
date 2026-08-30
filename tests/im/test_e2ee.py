"""Room encryption: Megolm over Olm, and what must not decrypt.

The assertion that matters most in this file is not that a message
round-trips. It is that a device **without** the room key cannot read it —
because that is the claim the screen will make once this is wired, and an
encryption test that only proves the sender can read their own message
proves nothing at all.

Everything here drives real vodozemac sessions between real `Device`
objects. There is no fake cipher and no injected key.
"""

from __future__ import annotations

import pytest

from treble.im.e2ee import (
    MEGOLM_ALGORITHM,
    OLM_ALGORITHM,
    Device,
    EncryptionError,
    RoomDecryptor,
    RoomSession,
)

ROOM = "!desk:treble.invalid"


def _paired() -> tuple[Device, Device]:
    """Two devices with an Olm session from the first to the second."""
    alice, bob = Device(), Device()
    published = bob.publish_one_time_keys(1)
    alice.establish(
        their_identity_key=bob.keys.curve25519,
        their_one_time_key=next(iter(published.values())),
    )
    return alice, bob


class TestOlm:
    def test_a_message_round_trips_between_two_devices(self) -> None:
        alice, bob = _paired()
        ciphertext = alice.encrypt_to(bob.keys.curve25519, b"the room key")
        assert bob.decrypt_from(alice.keys.curve25519, ciphertext) == b"the room key"

    def test_the_first_message_is_a_pre_key_message(self) -> None:
        """Type 0 carries what the recipient needs to build its side. A
        client that guessed the type would fail on exactly the first
        message, which is the one that sets everything up."""
        alice, bob = _paired()
        assert alice.encrypt_to(bob.keys.curve25519, b"hello")["type"] == 0

    def test_the_session_continues_after_the_first_message(self) -> None:
        alice, bob = _paired()
        first = alice.encrypt_to(bob.keys.curve25519, b"one")
        bob.decrypt_from(alice.keys.curve25519, first)
        second = alice.encrypt_to(bob.keys.curve25519, b"two")
        assert bob.decrypt_from(alice.keys.curve25519, second) == b"two"

    def test_a_third_device_cannot_read_it(self) -> None:
        alice, bob = _paired()
        eve = Device()
        ciphertext = alice.encrypt_to(bob.keys.curve25519, b"private")
        with pytest.raises(EncryptionError):
            eve.decrypt_from(alice.keys.curve25519, ciphertext)

    def test_encrypting_without_a_session_is_refused(self) -> None:
        alice, bob = Device(), Device()
        with pytest.raises(EncryptionError, match="no Olm session"):
            alice.encrypt_to(bob.keys.curve25519, b"hello")

    def test_messages_stay_pre_key_until_the_peer_replies(self) -> None:
        """Learned from the library rather than assumed: Olm keeps sending
        pre-key messages until it receives one back, because until then the
        sender has no evidence the recipient built its side. A client that
        expected type 1 after the first decrypt would be wrong about the
        protocol, not about this code."""
        alice, bob = _paired()
        first = alice.encrypt_to(bob.keys.curve25519, b"one")
        bob.decrypt_from(alice.keys.curve25519, first)
        assert alice.encrypt_to(bob.keys.curve25519, b"two")["type"] == 0

    def test_a_reply_completes_the_session_both_ways(self) -> None:
        alice, bob = _paired()
        bob.decrypt_from(alice.keys.curve25519, alice.encrypt_to(bob.keys.curve25519, b"hello"))
        back = bob.encrypt_to(alice.keys.curve25519, b"hello yourself")
        assert alice.decrypt_from(bob.keys.curve25519, back) == b"hello yourself"

    def test_malformed_ciphertext_is_refused(self) -> None:
        alice, bob = _paired()
        for bad in ({}, {"type": "0", "body": "x"}, {"type": 0, "body": 42}):
            with pytest.raises(EncryptionError):
                bob.decrypt_from(alice.keys.curve25519, bad)


class TestOneTimeKeys:
    def test_published_keys_are_marked_published(self) -> None:
        """An account that never marks them keeps handing out the same
        ones, and a one-time key used twice is not one-time — two peers
        would establish against the same key, which is exactly the reuse
        the ratchet's forward secrecy assumes cannot happen."""
        device = Device()
        first = device.publish_one_time_keys(2)
        second = device.publish_one_time_keys(2)
        assert set(first) & set(second) == set(), "a one-time key was offered twice"

    def test_the_device_advertises_both_algorithms(self) -> None:
        published = Device().keys.published(user_id="@a:x", device_id="D1")
        assert set(published["algorithms"]) == {OLM_ALGORITHM, MEGOLM_ALGORITHM}

    def test_the_published_payload_is_unsigned(self) -> None:
        """Signing is `crosssigning`'s concern. A module that both
        encrypted and attested to identity would make it easy to believe
        the second because the first worked."""
        published = Device().keys.published(user_id="@a:x", device_id="D1")
        assert "signatures" not in published


class TestMegolm:
    """Encryption and decryption work. Distribution over the wire does not —
    see `TestRoomKeyDistributionIsBlocked`."""

    def _shared(self) -> tuple[RoomSession, RoomDecryptor]:
        session = RoomSession(ROOM)
        decryptor = RoomDecryptor()
        decryptor.accept_session(session.session_id, session.live_key())
        return session, decryptor

    def test_a_room_message_round_trips(self) -> None:
        session, decryptor = self._shared()
        content = session.encrypt(b"buy 100 IBM", sender_key="k", device_id="D1")
        assert decryptor.decrypt(content) == b"buy 100 IBM"

    def test_a_device_without_the_key_cannot_read_it(self) -> None:
        """**The claim any screen would make.** An encryption test that only
        proves the sender can read their own message proves nothing."""
        session = RoomSession(ROOM)
        content = session.encrypt(b"buy 100 IBM", sender_key="k", device_id="D1")
        with pytest.raises(EncryptionError, match="no room key"):
            RoomDecryptor().decrypt(content)

    def test_the_key_is_captured_before_the_first_message(self) -> None:
        """Measured: `session_key` read after encrypting is the key at the
        new ratchet index, and a recipient given that cannot read anything
        earlier. Distributing the wrong one fails only when somebody reports
        a message they cannot read."""
        session = RoomSession(ROOM)
        first = session.encrypt(b"one", sender_key="k", device_id="D1")
        second = session.encrypt(b"two", sender_key="k", device_id="D1")

        decryptor = RoomDecryptor()
        decryptor.accept_session(session.session_id, session.live_key())
        assert decryptor.decrypt(first) == b"one"
        assert decryptor.decrypt(second) == b"two"

    def test_the_content_names_its_session(self) -> None:
        session = RoomSession(ROOM)
        content = session.encrypt(b"x", sender_key="sender-key", device_id="D1")
        assert content["session_id"] == session.session_id
        assert content["algorithm"] == MEGOLM_ALGORITHM
        assert content["sender_key"] == "sender-key"

    def test_a_second_key_for_one_session_does_not_replace_the_first(self) -> None:
        """Adopting it would silently move the readable window forward:
        messages this device could read would stop decrypting, with nothing
        to say why."""
        session, decryptor = self._shared()
        first = session.encrypt(b"early", sender_key="k", device_id="D1")
        later = RoomSession(ROOM)
        decryptor.accept_session(session.session_id, later.live_key())
        assert decryptor.decrypt(first) == b"early", "the original session was replaced"

    def test_malformed_content_is_refused(self) -> None:
        for bad in ({}, {"session_id": 1, "ciphertext": "x"}, {"session_id": "s"}):
            with pytest.raises(EncryptionError):
                RoomDecryptor().decrypt(bad)


class TestRoomKeyDistributionIsBlocked:
    """The measured limit, asserted so nothing can believe otherwise.

    `vodozemac` 0.10.0 exposes `to_base64` on `SessionKey` and
    `ExportedSessionKey` and `from_base64` on neither, and
    `InboundGroupSession` accepts nothing else. 0.10.0 is the latest.

    The consequence is asymmetric and disqualifying: this package could
    *send* a room key another client would import and could never *receive*
    one, so a Treble device cannot read a room anybody else encrypts.
    """

    def test_importing_a_wire_room_key_is_refused_by_name(self) -> None:
        session = RoomSession(ROOM)
        with pytest.raises(EncryptionError, match=r"no SessionKey\.from_base64"):
            RoomDecryptor().import_room_key(session.room_key())

    def test_the_refusal_says_olm_is_unaffected(self) -> None:
        """A reader hitting this must not conclude the whole module is
        broken: device-to-device encryption works completely."""
        session = RoomSession(ROOM)
        try:
            RoomDecryptor().import_room_key(session.room_key())
        except EncryptionError as exc:
            assert "Olm" in str(exc) and "works" in str(exc)

    def test_the_binding_really_lacks_the_constructor(self) -> None:
        """Pinned against the library itself, so the day it gains one this
        fails and the refusal above can be deleted."""
        import vodozemac

        assert not hasattr(vodozemac.SessionKey, "from_base64")
        assert not hasattr(vodozemac.ExportedSessionKey, "from_base64")

    def test_a_key_can_still_be_exported_for_others(self) -> None:
        """The half that does work: a real Matrix client could import this."""
        session = RoomSession(ROOM)
        assert len(session.room_key().session_key) > 100


class TestTheRoomKeyNeverTravelsInClear:
    """What the homeserver would see if a room key were sent today.

    The Olm half is complete, so this asserts the property that matters
    about it: the wrapped key is unreadable to anyone without the session.
    The receiving half cannot be exercised — see
    `TestRoomKeyDistributionIsBlocked` — so this stops where the binding
    does rather than pretending to finish.
    """

    def test_a_wrapped_room_key_is_opaque_and_unreadable_by_a_third_party(self) -> None:
        import json

        alice, bob = _paired()
        session = RoomSession(ROOM)
        wrapped = alice.encrypt_to(
            bob.keys.curve25519, json.dumps(session.room_key().content()).encode()
        )

        assert session.room_key().session_key not in wrapped["body"]
        assert "session_key" not in wrapped["body"]

        with pytest.raises(EncryptionError):
            Device().decrypt_from(alice.keys.curve25519, wrapped)

    def test_the_intended_recipient_recovers_the_key_material(self) -> None:
        """Olm delivers it intact. Only the Megolm import beyond this is
        blocked, which is a narrower failure than 'key distribution does not
        work' and worth keeping distinct."""
        import json

        alice, bob = _paired()
        session = RoomSession(ROOM)
        wrapped = alice.encrypt_to(
            bob.keys.curve25519, json.dumps(session.room_key().content()).encode()
        )
        received = json.loads(bob.decrypt_from(alice.keys.curve25519, wrapped))
        assert received["session_key"] == session.room_key().session_key
        assert received["room_id"] == ROOM
