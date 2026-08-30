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
    """Two clients verifying each other through to-device messaging.

    The verifier is only worth having if two real parties can reach the
    comparison step through the actual channel. To-device is that channel,
    and it exists precisely because two devices needing to verify each
    other may share no room — a verification that required one would be
    unavailable exactly when a new device is being set up.
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

    def test_two_clients_reach_the_same_string(self) -> None:
        """End to end: Alice starts, Bob syncs and sees her key, both
        establish, and the emoji agree."""
        _, alice, bob = self._two_clients()
        txn = "verify-1"

        hers = alice.start_verification(
            their_user=bob.user_id or "", their_device="LAPTOP2", transaction_id=txn
        )
        bob.sync()
        offered = bob.verification_keys(txn)
        assert alice.user_id in offered, "Bob did not receive Alice's key"

        his = Verification()
        bob.send_to_device(
            "m.key.verification.start",
            {
                alice.user_id or "": {
                    "DESKTOP1": {
                        "method": "m.sas.v1",
                        "transaction_id": txn,
                        "key": his.public_key,
                    }
                }
            },
        )
        alice.sync()
        back = alice.verification_keys(txn)

        alice_key = offered[alice.user_id or ""]
        bob_key = back[bob.user_id or ""]

        # Built once and used by both sides, which is the point of
        # `sas_info` being a function: two sides assembling it separately
        # is how the emoji come out different and the humans report an
        # attack that is not happening.
        info = sas_info(
            initiator_user=alice.user_id or "",
            initiator_device="DESKTOP1",
            initiator_key=alice_key,
            responder_user=bob.user_id or "",
            responder_device="LAPTOP2",
            responder_key=bob_key,
            transaction_id=txn,
        )
        assert (
            hers.establish(bob_key, info=info).emoji_indices
            == his.establish(alice_key, info=info).emoji_indices
        )

    def test_to_device_events_are_drained_not_replayed(self) -> None:
        """A client that received the same `start` twice would begin two
        exchanges for one request, and the second would never complete."""
        _, alice, bob = self._two_clients()
        alice.start_verification(
            their_user=bob.user_id or "", their_device="LAPTOP2", transaction_id="t1"
        )
        bob.sync()
        assert bob.verification_keys("t1")
        bob.sync()
        assert not bob.verification_keys("t1"), "the event was delivered twice"

    def test_another_transactions_key_is_not_returned(self) -> None:
        """Two verifications can be in flight. Taking whichever arrived
        last would let a third party's exchange be mistaken for the one the
        human is looking at."""
        _, alice, bob = self._two_clients()
        alice.start_verification(
            their_user=bob.user_id or "", their_device="LAPTOP2", transaction_id="t1"
        )
        bob.sync()
        assert bob.verification_keys("t2") == {}

    def test_a_verification_needs_no_shared_room(self) -> None:
        """The reason to-device exists. Neither client joins anything."""
        _, alice, bob = self._two_clients()
        assert alice.joined_rooms() == () and bob.joined_rooms() == ()
        alice.start_verification(
            their_user=bob.user_id or "", their_device="LAPTOP2", transaction_id="t1"
        )
        bob.sync()
        assert bob.verification_keys("t1")
