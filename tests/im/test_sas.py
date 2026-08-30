"""SAS verification: two sides, a shared string, and a human in the middle.

What makes this worth testing is the *ordering*. The cryptography is
vodozemac's and is not on trial here; what is on trial is that this module
cannot be walked into a verified state without the out-of-band step, and
that two sides who disagree about who they are talking to get different
strings rather than the same one.

Both sides are driven for real in every test — two `Verification` objects
exchanging keys — because a single-sided test can only check that a
function returns something, not that the two agree.
"""

from __future__ import annotations

import pytest

from treble.im.sas import (
    EMOJI,
    EMOJI_COUNT,
    MAC_INFO_PREFIX,
    SasError,
    ShortAuthString,
    Verification,
    sas_info,
)

ALICE, BOB = "@alice:treble.invalid", "@bob:treble.invalid"
DEVICE_A, DEVICE_B = "DESKTOP1", "LAPTOP2"
TXN = "txn-1"


def _info(alice: Verification, bob: Verification, *, transaction_id: str = TXN) -> str:
    return sas_info(
        initiator_user=ALICE,
        initiator_device=DEVICE_A,
        initiator_key=alice.public_key,
        responder_user=BOB,
        responder_device=DEVICE_B,
        responder_key=bob.public_key,
        transaction_id=transaction_id,
    )


def _paired(
    **kwargs: object,
) -> tuple[Verification, Verification, ShortAuthString, ShortAuthString]:
    """Two sides that have exchanged keys, with their derived strings."""
    alice, bob = Verification(), Verification()
    info = _info(alice, bob, **kwargs)  # type: ignore[arg-type]
    their_a, their_b = alice.public_key, bob.public_key
    return alice, bob, alice.establish(their_b, info=info), bob.establish(their_a, info=info)


class TestBothSidesAgree:
    def test_the_two_sides_derive_the_same_string(self) -> None:
        """The whole protocol in one assertion. If these differ, two humans
        reading them report an attack that is not happening."""
        _, _, a, b = _paired()
        assert a.emoji_indices == b.emoji_indices
        assert a.decimals == b.decimals

    def test_it_is_seven_emoji(self) -> None:
        """Specification's choice, not this module's: a client showing six
        compares a different string and every verification fails."""
        _, _, a, _ = _paired()
        assert len(a.emoji_indices) == EMOJI_COUNT == 7

    def test_the_names_travel_with_the_symbols(self) -> None:
        """Many terminals render an emoji as a box, and two people
        comparing boxes have verified nothing. The word is what is spoken."""
        _, _, a, _ = _paired()
        assert len(a.emoji) == EMOJI_COUNT
        assert all(symbol and name for symbol, name in a.emoji)
        assert len(a.spoken.split()) >= EMOJI_COUNT

    def test_two_independent_exchanges_differ(self) -> None:
        """Ephemeral keys, so the string is not a function of the
        participants alone. Identical strings across exchanges would mean
        an observer who saw one could replay it."""
        _, _, first, _ = _paired()
        _, _, second, _ = _paired()
        assert first.emoji_indices != second.emoji_indices


class TestTheInfoStringBindsTheExchange:
    def test_a_different_transaction_gives_a_different_string(self) -> None:
        """The binding is what stops a secret established with one party
        being replayed against another."""
        alice, bob = Verification(), Verification()
        their_b = bob.public_key
        one = alice.establish(their_b, info=_info(alice, bob, transaction_id="txn-1"))

        carol, dave = Verification(), Verification()
        their_d = dave.public_key
        two = carol.establish(their_d, info=_info(carol, dave, transaction_id="txn-2"))
        assert one.emoji_indices != two.emoji_indices

    def test_sides_that_disagree_about_the_info_get_different_strings(self) -> None:
        """**The safe failure direction.** Two implementations assembling
        the info differently do not silently verify — they produce
        different emoji, the humans see a mismatch, and nobody is
        verified."""
        alice, bob = Verification(), Verification()
        their_a, their_b = alice.public_key, bob.public_key
        mine = alice.establish(their_b, info="MATRIX_KEY_VERIFICATION_SAS|a|b")
        theirs = bob.establish(their_a, info="MATRIX_KEY_VERIFICATION_SAS|b|a")
        assert mine.emoji_indices != theirs.emoji_indices

    def test_the_info_names_both_parties_and_the_transaction(self) -> None:
        alice, bob = Verification(), Verification()
        info = _info(alice, bob)
        for part in (ALICE, BOB, DEVICE_A, DEVICE_B, TXN):
            assert part in info


class TestTheHumanCannotBeSkipped:
    def test_a_mac_before_confirmation_is_refused(self) -> None:
        """**The defect this ordering prevents.** A MAC commits to a device
        key; calculating one before a human has compared the emoji binds to
        a secret that might have been established with an attacker — the
        whole failure SAS prevents, reached through the back door."""
        alice, _, _, _ = _paired()
        with pytest.raises(SasError, match="before the humans have compared"):
            alice.mac(key="ed25519:DESKTOP1", info=MAC_INFO_PREFIX)

    def test_checking_a_mac_before_confirmation_is_refused(self) -> None:
        """Accepting the other side's MAC is accepting their key."""
        alice, _, _, _ = _paired()
        with pytest.raises(SasError, match="before the humans have compared"):
            alice.verify_mac(mac="whatever", key="ed25519:LAPTOP2", info=MAC_INFO_PREFIX)

    def test_confirmation_takes_the_humans_answer_as_an_argument(self) -> None:
        """This module cannot make the comparison. One that decided for
        itself would be asserting the thing SAS exists to establish."""
        alice, _, _, _ = _paired()
        assert alice.confirmed is False
        assert alice.confirm(humans_agreed=True) is True
        assert alice.confirmed is True

    def test_a_human_saying_no_leaves_it_unconfirmed(self) -> None:
        alice, _, _, _ = _paired()
        alice.confirm(humans_agreed=False)
        assert not alice.confirmed
        with pytest.raises(SasError, match="before the humans have compared"):
            alice.mac(key="ed25519:DESKTOP1", info=MAC_INFO_PREFIX)

    def test_confirming_before_establishing_is_refused(self) -> None:
        with pytest.raises(SasError, match="not established"):
            Verification().confirm(humans_agreed=True)


class TestMacsBindTheKeys:
    def test_a_mac_round_trips_between_the_two_sides(self) -> None:
        """After the humans agree, each side commits to its key and the
        other checks it. This is what turns 'the strings matched' into
        'and these are the keys they matched for'."""
        alice, bob, _, _ = _paired()
        alice.confirm(humans_agreed=True)
        bob.confirm(humans_agreed=True)

        key = "ed25519:DESKTOP1"
        mac = alice.mac(key=key, info=MAC_INFO_PREFIX)
        assert bob.verify_mac(mac=mac, key=key, info=MAC_INFO_PREFIX)

    def test_a_mac_over_a_different_key_does_not_verify(self) -> None:
        """The point of the MAC: it commits to *which* key, so an attacker
        cannot substitute one after the humans have agreed."""
        alice, bob, _, _ = _paired()
        alice.confirm(humans_agreed=True)
        bob.confirm(humans_agreed=True)

        mac = alice.mac(key="ed25519:DESKTOP1", info=MAC_INFO_PREFIX)
        assert not bob.verify_mac(mac=mac, key="ed25519:ATTACKER", info=MAC_INFO_PREFIX)

    def test_a_mac_from_an_unrelated_exchange_does_not_verify(self) -> None:
        alice, bob, _, _ = _paired()
        stranger, _, _, _ = _paired()
        for side in (alice, bob, stranger):
            side.confirm(humans_agreed=True)

        key = "ed25519:DESKTOP1"
        assert not bob.verify_mac(
            mac=stranger.mac(key=key, info=MAC_INFO_PREFIX), key=key, info=MAC_INFO_PREFIX
        )

    def test_a_malformed_mac_is_false_not_an_exception(self) -> None:
        _, bob, _, _ = _paired()
        bob.confirm(humans_agreed=True)
        assert not bob.verify_mac(mac="not-base64!", key="k", info=MAC_INFO_PREFIX)


class TestSingleUse:
    def test_the_ephemeral_key_is_consumed(self) -> None:
        """`vodozemac.Sas` is single-use by construction. An exchange that
        could be re-established with a second party is one where the first
        party's secret is not ephemeral."""
        alice, bob = Verification(), Verification()
        alice.establish(bob.public_key, info=_info(alice, bob))
        with pytest.raises(SasError, match="already been established"):
            alice.establish(Verification().public_key, info="x")

    def test_the_public_key_is_gone_after_establishing(self) -> None:
        alice, bob = Verification(), Verification()
        alice.establish(bob.public_key, info=_info(alice, bob))
        with pytest.raises(SasError, match="already been established"):
            _ = alice.public_key

    def test_an_unusable_peer_key_is_refused(self) -> None:
        with pytest.raises(SasError, match="not usable"):
            Verification().establish("not-a-key", info="x")


class TestTheEmojiTable:
    def test_it_has_sixty_four_entries(self) -> None:
        """Seven from 64 is 42 bits of comparison. A shorter table weakens
        every verification made with it."""
        assert len(EMOJI) == 64

    def test_every_entry_has_a_symbol_and_a_name(self) -> None:
        assert all(symbol and name for symbol, name in EMOJI)

    def test_the_names_are_distinct(self) -> None:
        """Two entries reading the same word would make a mismatch
        indistinguishable from a match to the humans speaking them."""
        assert len({name for _, name in EMOJI}) == len(EMOJI)

    def test_an_index_outside_the_table_is_refused(self) -> None:
        with pytest.raises(SasError, match="outside the table"):
            ShortAuthString(emoji_indices=(0, 1, 2, 3, 4, 5, 99), decimals=(1, 2, 3))

    def test_the_wrong_number_of_emoji_is_refused(self) -> None:
        with pytest.raises(SasError, match="is 7 emoji"):
            ShortAuthString(emoji_indices=(0, 1, 2), decimals=(1, 2, 3))


class TestOrderIsPreserved:
    """Found by mutation testing: sorting the indices passed every test above.

    Both sides still agreed (both would sort), it was still seven emoji,
    and two exchanges still differed — so nothing caught it. But the order
    carries information: Matrix compares an *ordered* sequence, so a client
    that sorted would mismatch every other client, and sorting collapses
    the comparison space from 64**7 ordered sequences to the far smaller
    number of unordered multisets.
    """

    def test_the_indices_are_the_primitives_own_order(self) -> None:
        """Compared against vodozemac directly, not against this module.

        Driving both halves of one exchange here means the expected bytes
        come from the library rather than from the code under test, so a
        module that reordered them fails even though its two sides would
        still agree with each other.
        """
        import vodozemac

        from treble.im.sas import EMOJI_COUNT

        info = "MATRIX_KEY_VERIFICATION_SAS|fixed|info"

        # One real exchange: this module on one side, the bare library on
        # the other. ECDH is symmetric, so both derive the same secret and
        # must report the same sequence — and the expected value comes from
        # vodozemac rather than from the code under test.
        side = Verification()
        peer = vodozemac.Sas()
        side_key = vodozemac.Curve25519PublicKey.from_base64(side.public_key)

        produced = side.establish(peer.public_key.to_base64(), info=info)
        expected = tuple(peer.diffie_hellman(side_key).bytes(info).emoji_indices[:EMOJI_COUNT])

        assert produced.emoji_indices == expected, "the module reordered the SAS"


class TestOverTheHomeserver:
    """Two clients verifying through to-device messaging, with commitments.

    To-device is the channel this must run over: two devices needing to
    verify each other may share no room, and a verification that required
    one would be unavailable exactly when a new device is being set up.
    """

    def _two_clients(self):  # type: ignore[no-untyped-def]
        from treble.im.matrix import MatrixClient
        from treble.im.simulator import Homeserver

        server = Homeserver()
        server.accounts["alice"] = "pw-a"
        server.accounts["bob"] = "pw-b"
        a = MatrixClient(transport=server.transport)
        b = MatrixClient(transport=server.transport)
        a.login(user="alice", password="pw-a")  # noqa: S106
        b.login(user="bob", password="pw-b")  # noqa: S106
        return server, a, b

    def test_a_full_exchange_reaches_the_same_string(self) -> None:
        """start -> accept(commitment) -> key -> key, then both derive."""
        from treble.im.sas import verify_commitment

        _, alice, bob = self._two_clients()
        txn = "verify-1"
        alice_id, bob_id = alice.user_id or "", bob.user_id or ""

        start = alice.start_verification(
            their_user=bob_id, their_device="LAPTOP2", transaction_id=txn
        )
        bob.sync()
        assert bob.verification_event("m.key.verification.start", txn) == start

        his = bob.accept_verification(
            their_user=alice_id,
            their_device="DESKTOP1",
            transaction_id=txn,
            start_content=start,
        )
        alice.sync()
        accept = alice.verification_event("m.key.verification.accept", txn)
        assert accept is not None
        bob_key = alice.verification_keys(txn)[bob_id]

        # The initiator checks the commitment before going further.
        assert verify_commitment(
            public_key=bob_key, start_content=start, claimed=accept["commitment"]
        )

        hers = Verification()
        alice.send_verification_key(
            their_user=bob_id,
            their_device="LAPTOP2",
            transaction_id=txn,
            public_key=hers.public_key,
        )
        bob.sync()
        alice_key = bob.verification_keys(txn)[alice_id]

        info = sas_info(
            initiator_user=alice_id,
            initiator_device="DESKTOP1",
            initiator_key=alice_key,
            responder_user=bob_id,
            responder_device="LAPTOP2",
            responder_key=bob_key,
            transaction_id=txn,
        )
        assert (
            hers.establish(bob_key, info=info).emoji_indices
            == his.establish(alice_key, info=info).emoji_indices
        )

    def test_a_responder_that_swaps_its_key_is_caught(self) -> None:
        """**The attack the commitment exists to stop.** A responder free
        to choose after seeing the initiator's key can grind keys until the
        emoji come out however it likes, and steer two humans onto a
        sequence it prepared. The commitment binds it first."""
        from treble.im.sas import verify_commitment

        _, alice, bob = self._two_clients()
        txn = "verify-2"
        start = alice.start_verification(
            their_user=bob.user_id or "", their_device="LAPTOP2", transaction_id=txn
        )
        bob.sync()
        bob.accept_verification(
            their_user=alice.user_id or "",
            their_device="DESKTOP1",
            transaction_id=txn,
            start_content=start,
        )
        alice.sync()
        accept = alice.verification_event("m.key.verification.accept", txn)
        assert accept is not None

        substituted = Verification().public_key
        assert not verify_commitment(
            public_key=substituted, start_content=start, claimed=accept["commitment"]
        ), "a swapped key must not match the commitment"

    def test_an_accept_for_another_request_does_not_match(self) -> None:
        """The commitment covers the `start` content too, so an accept
        cannot be replayed from a different request."""
        from treble.im.sas import commitment, verify_commitment

        key = Verification().public_key
        one = {"method": "m.sas.v1", "transaction_id": "a"}
        two = {"method": "m.sas.v1", "transaction_id": "b"}
        assert not verify_commitment(
            public_key=key, start_content=two, claimed=commitment(key, one)
        )

    def test_the_start_event_carries_no_key(self) -> None:
        """An earlier cut put it there, which skips the commitment step and
        gives away the property that step exists for."""
        _, alice, bob = self._two_clients()
        start = alice.start_verification(
            their_user=bob.user_id or "", their_device="LAPTOP2", transaction_id="t"
        )
        assert "key" not in start

    def test_to_device_events_are_drained_not_replayed(self) -> None:
        """A client receiving the same `start` twice would begin two
        exchanges for one request."""
        _, alice, bob = self._two_clients()
        alice.start_verification(
            their_user=bob.user_id or "", their_device="LAPTOP2", transaction_id="t1"
        )
        bob.sync()
        assert bob.verification_event("m.key.verification.start", "t1")
        bob.sync()
        assert bob.verification_event("m.key.verification.start", "t1") is None

    def test_another_transactions_event_is_not_returned(self) -> None:
        _, alice, bob = self._two_clients()
        alice.start_verification(
            their_user=bob.user_id or "", their_device="LAPTOP2", transaction_id="t1"
        )
        bob.sync()
        assert bob.verification_event("m.key.verification.start", "t2") is None

    def test_a_verification_needs_no_shared_room(self) -> None:
        """The reason to-device exists. Neither client joins anything."""
        _, alice, bob = self._two_clients()
        assert alice.joined_rooms() == () and bob.joined_rooms() == ()
        alice.start_verification(
            their_user=bob.user_id or "", their_device="LAPTOP2", transaction_id="t1"
        )
        bob.sync()
        assert bob.verification_event("m.key.verification.start", "t1")


class TestTheCommitmentWireFormat:
    """Found by mutation testing: padded base64 passed everything above.

    Both sides ran this implementation, so the padding stayed consistent
    and every comparison agreed. Against any other Matrix client it would
    not — the specification says unpadded, and a padded string is simply a
    different value. Self-consistency cannot catch an interoperability
    property, so the shape is pinned directly.
    """

    def test_it_is_unpadded(self) -> None:
        from treble.im.sas import commitment

        value = commitment(Verification().public_key, {"method": "m.sas.v1"})
        assert "=" not in value, "padded base64 disagrees with every other client"

    def test_it_is_a_sha256_digest(self) -> None:
        """32 bytes is 43 unpadded base64 characters. A different length
        means a different hash function, which would agree with nobody."""
        from treble.im.sas import commitment

        value = commitment(Verification().public_key, {"method": "m.sas.v1"})
        assert len(value) == 43

    def test_it_matches_the_specifications_construction(self) -> None:
        """SHA-256 over the key bytes followed by the canonical JSON of the
        start content, computed here independently of the module."""
        import base64
        import hashlib

        from treble.im.canonical import canonical_json
        from treble.im.sas import commitment

        key = Verification().public_key
        start = {"method": "m.sas.v1", "transaction_id": "t"}
        expected = (
            base64.b64encode(hashlib.sha256(key.encode() + canonical_json(start)).digest())
            .decode()
            .rstrip("=")
        )
        assert commitment(key, start) == expected


class TestTheMacExchange:
    """The last step: each side commits to the keys it actually holds.

    The emoji established that the two secrets match. The MACs establish
    *which keys* that agreement was about — without them two humans have
    confirmed a shared secret and learned nothing about anybody's device.
    """

    def _confirmed_pair(self):  # type: ignore[no-untyped-def]
        from treble.im.matrix import MatrixClient
        from treble.im.simulator import Homeserver

        server = Homeserver()
        server.accounts["alice"] = "pw-a"
        server.accounts["bob"] = "pw-b"
        alice = MatrixClient(transport=server.transport)
        bob = MatrixClient(transport=server.transport)
        alice.login(user="alice", password="pw-a")  # noqa: S106
        bob.login(user="bob", password="pw-b")  # noqa: S106

        hers, his = Verification(), Verification()
        info = sas_info(
            initiator_user=alice.user_id or "",
            initiator_device="DESKTOP1",
            initiator_key=hers.public_key,
            responder_user=bob.user_id or "",
            responder_device="LAPTOP2",
            responder_key=his.public_key,
            transaction_id="t",
        )
        their_h, their_hi = hers.public_key, his.public_key
        hers.establish(their_hi, info=info)
        his.establish(their_h, info=info)
        hers.confirm(humans_agreed=True)
        his.confirm(humans_agreed=True)
        return alice, bob, hers, his

    def test_a_mac_round_trips_over_to_device(self) -> None:
        alice, bob, hers, his = self._confirmed_pair()
        keys = {"ed25519:DESKTOP1": "alice-device-key"}

        alice.send_verification_mac(
            hers,
            their_user=bob.user_id or "",
            their_device="LAPTOP2",
            my_device="DESKTOP1",
            transaction_id="t",
            keys=keys,
        )
        bob.sync()
        content = bob.verification_event("m.key.verification.mac", "t")
        assert content is not None
        assert bob.verify_verification_mac(
            his,
            their_user=alice.user_id or "",
            their_device="DESKTOP1",
            my_device="LAPTOP2",
            transaction_id="t",
            content=content,
            claimed_keys=keys,
        )

    def test_a_stripped_key_is_caught_by_the_keys_mac(self) -> None:
        """**What the second MAC is for.** An attacker deleting an entry
        from `mac` in flight would otherwise be undetectable: a stripped
        key is indistinguishable from a key the peer never claimed, and the
        receiver verifies a smaller set than the sender sent while both
        sides believe they agreed."""
        alice, bob, hers, his = self._confirmed_pair()
        keys = {"ed25519:DESKTOP1": "device-key", "ed25519:MASTER": "master-key"}

        content = alice.send_verification_mac(
            hers,
            their_user=bob.user_id or "",
            their_device="LAPTOP2",
            my_device="DESKTOP1",
            transaction_id="t",
            keys=keys,
        )
        tampered = dict(content)
        tampered["mac"] = {"ed25519:DESKTOP1": content["mac"]["ed25519:DESKTOP1"]}

        assert not bob.verify_verification_mac(
            his,
            their_user=alice.user_id or "",
            their_device="DESKTOP1",
            my_device="LAPTOP2",
            transaction_id="t",
            content=tampered,
            claimed_keys={"ed25519:DESKTOP1": "device-key"},
        )

    def test_a_substituted_key_value_does_not_verify(self) -> None:
        alice, bob, hers, his = self._confirmed_pair()
        content = alice.send_verification_mac(
            hers,
            their_user=bob.user_id or "",
            their_device="LAPTOP2",
            my_device="DESKTOP1",
            transaction_id="t",
            keys={"ed25519:DESKTOP1": "the-real-key"},
        )
        assert not bob.verify_verification_mac(
            his,
            their_user=alice.user_id or "",
            their_device="DESKTOP1",
            my_device="LAPTOP2",
            transaction_id="t",
            content=content,
            claimed_keys={"ed25519:DESKTOP1": "an-attackers-key"},
        )

    def test_a_reflected_mac_does_not_verify(self) -> None:
        """`mac_info` is directional: each side MACs its own keys. A
        symmetric info would let a MAC be bounced back at its author and
        verify, and the receiver would conclude the peer holds a key it had
        only ever seen itself send."""
        alice, bob, hers, _ = self._confirmed_pair()
        keys = {"ed25519:DESKTOP1": "alice-device-key"}
        content = alice.send_verification_mac(
            hers,
            their_user=bob.user_id or "",
            their_device="LAPTOP2",
            my_device="DESKTOP1",
            transaction_id="t",
            keys=keys,
        )
        # Alice checks her own MAC as though Bob had sent it.
        assert not alice.verify_verification_mac(
            hers,
            their_user=bob.user_id or "",
            their_device="LAPTOP2",
            my_device="DESKTOP1",
            transaction_id="t",
            content=content,
            claimed_keys=keys,
        )

    def test_a_malformed_mac_event_is_false(self) -> None:
        _, bob, _, his = self._confirmed_pair()
        for content in ({}, {"mac": "not a dict"}, {"mac": {}, "keys": 42}):
            assert not bob.verify_verification_mac(
                his,
                their_user="@alice:treble.invalid",
                their_device="DESKTOP1",
                my_device="LAPTOP2",
                transaction_id="t",
                content=content,
                claimed_keys={},
            )

    def test_done_carries_no_proof_and_is_not_treated_as_any(self) -> None:
        """A side that never sends `done` has not thereby failed
        verification. The MACs are what established anything."""
        alice, bob, _, _ = self._confirmed_pair()
        alice.send_verification_done(
            their_user=bob.user_id or "", their_device="LAPTOP2", transaction_id="t"
        )
        bob.sync()
        content = bob.verification_event("m.key.verification.done", "t")
        assert content == {"transaction_id": "t"}


class TestTheMacWireFormat:
    """Three mutants survived the first pass here, and two were the same
    blind spot as the emoji sort and the padded base64 before them.

    Every test in this file drives both sides with this implementation, so
    a wire-format choice — the info string, the ordering inside it — stays
    invisible: both sides make the same choice and agree with each other
    while agreeing with no other Matrix client. Self-consistency cannot
    catch an interoperability property, so the format is pinned against an
    independent construction rather than against itself.

    The third survivor was a genuine logic gap and is covered here too.
    """

    def _pair(self):  # type: ignore[no-untyped-def]
        from treble.im.matrix import MatrixClient
        from treble.im.simulator import Homeserver

        server = Homeserver()
        server.accounts["alice"] = "pw-a"
        server.accounts["bob"] = "pw-b"
        alice = MatrixClient(transport=server.transport)
        bob = MatrixClient(transport=server.transport)
        alice.login(user="alice", password="pw-a")  # noqa: S106
        bob.login(user="bob", password="pw-b")  # noqa: S106

        hers, his = Verification(), Verification()
        their_h, their_hi = hers.public_key, his.public_key
        hers.establish(their_hi, info="i")
        his.establish(their_h, info="i")
        hers.confirm(humans_agreed=True)
        his.confirm(humans_agreed=True)
        return alice, bob, hers, his

    def test_the_key_ids_info_is_the_base_plus_a_literal_marker(self) -> None:
        """`base` alone is still unique among the per-key infos, so both
        sides would agree on it — and disagree with every other client."""
        from treble.im.sas import key_ids_mac_info

        assert key_ids_mac_info("BASE") == "BASEKEY_IDS"

    def test_the_mac_info_is_the_specifications_concatenation(self) -> None:
        """Built here independently of the module."""
        from treble.im.sas import MAC_INFO_PREFIX, mac_info

        produced = mac_info(
            sender_user="@a:x",
            sender_device="D1",
            receiver_user="@b:x",
            receiver_device="D2",
            transaction_id="t",
        )
        assert produced == f"{MAC_INFO_PREFIX}@a:xD1@b:xD2t"

    def test_the_keys_mac_is_over_sorted_ids(self) -> None:
        """Two dicts holding the same keys in different insertion order
        must produce the same `keys` MAC. Unsorted, they would not — and a
        peer that happened to insert differently would fail every
        verification for a reason nobody could see."""
        alice, bob, hers, _ = self._pair()
        forward = {"ed25519:AAA": "one", "ed25519:BBB": "two"}
        backward = {"ed25519:BBB": "two", "ed25519:AAA": "one"}

        first = alice.send_verification_mac(
            hers,
            their_user=bob.user_id or "",
            their_device="LAPTOP2",
            my_device="DESKTOP1",
            transaction_id="t",
            keys=forward,
        )
        second = alice.send_verification_mac(
            hers,
            their_user=bob.user_id or "",
            their_device="LAPTOP2",
            my_device="DESKTOP1",
            transaction_id="t",
            keys=backward,
        )
        assert first["keys"] == second["keys"], "the keys MAC depends on insertion order"

    def test_a_mac_naming_a_key_we_do_not_know_is_false_not_an_error(self) -> None:
        """The logic gap. Without the set check, an extra entry in `mac`
        reaches a `claimed_keys[key_id]` lookup and raises KeyError — an
        exception where the caller asked a yes/no question, and one the
        happy path would have to catch."""
        alice, bob, hers, his = self._pair()
        content = alice.send_verification_mac(
            hers,
            their_user=bob.user_id or "",
            their_device="LAPTOP2",
            my_device="DESKTOP1",
            transaction_id="t",
            keys={"ed25519:DESKTOP1": "k", "ed25519:SURPRISE": "extra"},
        )
        assert (
            bob.verify_verification_mac(
                his,
                their_user=alice.user_id or "",
                their_device="DESKTOP1",
                my_device="LAPTOP2",
                transaction_id="t",
                content=content,
                claimed_keys={"ed25519:DESKTOP1": "k"},
            )
            is False
        )
