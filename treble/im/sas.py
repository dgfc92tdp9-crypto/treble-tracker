"""SAS verification — confirming a key with the human at the other end (P3_1).

`im/crosssigning.py` checks that a device is signed by a master key, and
says plainly what it cannot do: **the master key comes from the
homeserver**, so a server willing to lie could serve its own chain and the
check would pass. That weakness is stated there rather than hidden, and
this module is what closes it.

Short Authentication String verification does not trust the server at all.
Two parties do an ephemeral Diffie-Hellman, derive a short string from the
shared secret, and **compare it out of band** — over the phone, across a
desk. A server in the middle cannot make two different secrets produce the
same seven emoji, so if the strings match, the keys are the ones the two
humans think they are.

## What this module cannot do, and why that is the point

It cannot perform the comparison. A human reads the string aloud and
another human confirms it, and no amount of code here substitutes for
that: an implementation that "verified" without the out-of-band step would
be asserting exactly the thing SAS exists to establish. So `confirm()`
takes the human's answer as an argument, and the type makes it impossible
to reach a verified state without one.

## The info string binds the exchange to its participants

Matrix derives the short string from the shared secret *and* an info
string naming both users, both devices, both public keys and the
transaction. That binding is what stops a secret established with one
party being replayed against another. Two sides that build the info string
differently get different emoji and the comparison fails — which is the
safe direction, and is why `sas_info` is one function both sides call
rather than a string each assembles.

## vodozemac (ADR-0011)

`Sas.diffie_hellman` and `EstablishedSas.bytes` do the cryptography;
`calculate_mac` / `verify_mac` bind the actual device keys once the humans
have agreed. Everything here is the protocol around them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

import vodozemac

from treble.im.canonical import canonical_json

#: The Matrix SAS emoji table, transcribed from the specification. Index
#: order is normative: a table in a different order produces names that
#: disagree with every other client, and two people reading different words
#: for the same emoji will report a mismatch that is not one.
EMOJI: tuple[tuple[str, str], ...] = (
    ("🐶", "Dog"),
    ("🐱", "Cat"),
    ("🦁", "Lion"),
    ("🐎", "Horse"),
    ("🦄", "Unicorn"),
    ("🐷", "Pig"),
    ("🐘", "Elephant"),
    ("🐰", "Rabbit"),
    ("🐼", "Panda"),
    ("🐓", "Rooster"),
    ("🐧", "Penguin"),
    ("🐢", "Turtle"),
    ("🐟", "Fish"),
    ("🐙", "Octopus"),
    ("🦋", "Butterfly"),
    ("🌷", "Flower"),
    ("🌳", "Tree"),
    ("🌵", "Cactus"),
    ("🍄", "Mushroom"),
    ("🌏", "Globe"),
    ("🌙", "Moon"),
    ("☁️", "Cloud"),
    ("🔥", "Fire"),
    ("🍌", "Banana"),
    ("🍎", "Apple"),
    ("🍓", "Strawberry"),
    ("🌽", "Corn"),
    ("🍕", "Pizza"),
    ("🎂", "Cake"),
    ("❤️", "Heart"),
    ("🙂", "Smiley"),
    ("🤖", "Robot"),
    ("🎩", "Hat"),
    ("👓", "Glasses"),
    ("🔧", "Spanner"),
    ("🎅", "Santa"),
    ("👍", "Thumbs up"),
    ("☂️", "Umbrella"),
    ("⌛", "Hourglass"),
    ("⏰", "Clock"),
    ("🎁", "Gift"),
    ("💡", "Light bulb"),
    ("📕", "Book"),
    ("✏️", "Pencil"),
    ("📎", "Paperclip"),
    ("✂️", "Scissors"),
    ("🔒", "Lock"),
    ("🔑", "Key"),
    ("🔨", "Hammer"),
    ("☎️", "Telephone"),
    ("🏁", "Flag"),
    ("🚂", "Train"),
    ("🚲", "Bicycle"),
    ("✈️", "Aeroplane"),
    ("🚀", "Rocket"),
    ("🏆", "Trophy"),
    ("⚽", "Ball"),
    ("🎸", "Guitar"),
    ("🎺", "Trumpet"),
    ("🔔", "Bell"),
    ("⚓", "Anchor"),
    ("🎧", "Headphones"),
    ("📁", "Folder"),
    ("📌", "Pin"),
)

#: How many emoji a Matrix SAS shows. Seven from a table of 64 is 42 bits
#: of comparison, which is the specification's choice and not this
#: module's to vary: a client showing six would be comparing a different
#: string from one showing seven and every verification would fail.
EMOJI_COUNT = 7

#: The specification's info prefix for the SAS derivation.
SAS_INFO_PREFIX = "MATRIX_KEY_VERIFICATION_SAS"

#: The info prefix for the MACs exchanged after the humans agree.
MAC_INFO_PREFIX = "MATRIX_KEY_VERIFICATION_MAC"


class SasError(ValueError):
    """The verification cannot proceed, and why."""


def commitment(public_key: str, start_content: dict[str, Any]) -> str:
    """The responder's binding commitment to its key and the request.

    **Why the protocol has this step at all.** Without it the responder
    could wait to see the initiator's public key and only then choose its
    own — and since the emoji are derived from both, a responder free to
    pick afterwards can grind keys until the string comes out however it
    likes. It could steer two humans onto a sequence it had prepared.

    So the responder commits first: `SHA-256(public_key || canonical_json(
    start_content))`, sent in `m.key.verification.accept` before either key
    is on the wire. The initiator checks it once the key arrives, and a
    responder that changed its mind is caught.

    Unpadded base64, as the specification requires. Padding would be a
    different string and the two sides' comparison would fail on every
    exchange.
    """
    digest = hashlib.sha256(public_key.encode() + canonical_json(start_content)).digest()
    return base64.b64encode(digest).decode().rstrip("=")


def verify_commitment(*, public_key: str, start_content: dict[str, Any], claimed: str) -> bool:
    """Whether ``claimed`` is the commitment for this key and request.

    Compared with `hmac.compare_digest` rather than `==`. The values are
    public, so this is not about timing — it is that a constant-time
    comparison is the correct habit for anything a peer supplies, and the
    exception here would have to be argued rather than assumed.
    """
    return hmac.compare_digest(commitment(public_key, start_content), claimed)


def mac_info(
    *,
    sender_user: str,
    sender_device: str,
    receiver_user: str,
    receiver_device: str,
    transaction_id: str,
) -> str:
    """The base info for the MACs exchanged after the humans agree.

    Directional, unlike `sas_info`: each side MACs *its own* keys, so the
    sender comes first and the two sides build different strings. A
    symmetric info here would let a MAC be reflected back at its author and
    verify — the receiver would conclude the peer holds a key it had only
    ever seen itself send.
    """
    return (
        f"{MAC_INFO_PREFIX}{sender_user}{sender_device}"
        f"{receiver_user}{receiver_device}{transaction_id}"
    )


def key_ids_mac_info(base: str) -> str:
    """Info for the MAC over the *set* of key ids.

    Matrix MACs the comma-joined sorted key ids as well as each key, and
    that second MAC is what stops an attacker deleting an entry from the
    `mac` object in flight. Without it a stripped key is indistinguishable
    from a key the peer never claimed, and the receiver verifies a smaller
    set than the sender sent while both sides believe they agreed.
    """
    return f"{base}KEY_IDS"


def sas_info(
    *,
    initiator_user: str,
    initiator_device: str,
    initiator_key: str,
    responder_user: str,
    responder_device: str,
    responder_key: str,
    transaction_id: str,
) -> str:
    """The info string both sides derive their emoji from.

    One function rather than a string each side assembles, because the two
    must agree exactly and the failure when they do not is a mismatch the
    humans will read as an attack. Ordered by *role* — initiator first —
    not by whoever is running the code, so both callers pass the same
    arguments in the same order and get the same string.
    """
    return (
        f"{SAS_INFO_PREFIX}|{initiator_user}|{initiator_device}|{initiator_key}"
        f"|{responder_user}|{responder_device}|{responder_key}|{transaction_id}"
    )


@dataclass(frozen=True)
class ShortAuthString:
    """What the two humans read to each other."""

    emoji_indices: tuple[int, ...]
    decimals: tuple[int, ...]

    @property
    def emoji(self) -> tuple[tuple[str, str], ...]:
        """(symbol, name) pairs, in order.

        The name travels with the symbol because a great many terminals
        render an emoji as a box, and two people comparing boxes have
        verified nothing. The word is what actually gets spoken.
        """
        return tuple(EMOJI[index] for index in self.emoji_indices)

    @property
    def spoken(self) -> str:
        """The string as it would be read aloud."""
        return " ".join(name for _, name in self.emoji)

    def __post_init__(self) -> None:
        if len(self.emoji_indices) != EMOJI_COUNT:
            raise SasError(
                f"a Matrix SAS is {EMOJI_COUNT} emoji, not {len(self.emoji_indices)}; "
                "a client showing a different number compares a different string"
            )
        for index in self.emoji_indices:
            if not 0 <= index < len(EMOJI):
                raise SasError(f"emoji index {index} is outside the table of {len(EMOJI)}")


class Verification:
    """One side of a SAS exchange.

    Deliberately stateful and deliberately one-shot. The protocol has an
    order — establish, compare, confirm, then MAC — and a type that let a
    caller take them out of order would let a MAC be trusted before any
    human had looked at anything.
    """

    def __init__(self) -> None:
        self._sas: vodozemac.Sas | None = vodozemac.Sas()
        self._established: vodozemac.EstablishedSas | None = None
        self._confirmed = False

    @property
    def public_key(self) -> str:
        """This side's ephemeral public key, to send to the other."""
        if self._sas is None:
            raise SasError("the exchange has already been established")
        return self._sas.public_key.to_base64()

    @property
    def confirmed(self) -> bool:
        """Whether a human has said the strings matched."""
        return self._confirmed

    def establish(self, their_public_key: str, *, info: str) -> ShortAuthString:
        """Complete the Diffie-Hellman and derive the string to compare.

        The ephemeral key is consumed here — `vodozemac.Sas` is single-use
        by construction, and this surfaces that rather than hiding it: an
        exchange that could be re-established with a second party is one
        where the first party's secret is not ephemeral.
        """
        if self._sas is None:
            raise SasError("this exchange has already been established")
        try:
            self._established = self._sas.diffie_hellman(
                vodozemac.Curve25519PublicKey.from_base64(their_public_key)
            )
        except Exception as exc:
            raise SasError(f"the other side's key is not usable: {exc}") from exc
        self._sas = None
        material = self._established.bytes(info)
        return ShortAuthString(
            emoji_indices=tuple(material.emoji_indices[:EMOJI_COUNT]),
            decimals=tuple(material.decimals),
        )

    def confirm(self, *, humans_agreed: bool) -> bool:
        """Record what the humans said.

        Takes the answer as an argument because **this module cannot make
        it**. The comparison happens over the phone or across a desk, and
        an implementation that decided it for itself would be asserting the
        one thing SAS exists to establish.
        """
        if self._established is None:
            raise SasError("nothing to confirm: the exchange is not established")
        self._confirmed = humans_agreed
        return self._confirmed

    def mac(self, *, key: str, info: str) -> str:
        """A MAC binding ``key`` to this exchange.

        Refused before confirmation. The MAC is what actually commits to a
        device key, and calculating one before a human has compared the
        emoji would produce a binding to a secret that might have been
        established with an attacker — which is the whole failure SAS
        prevents, arrived at through the back door.
        """
        if self._established is None:
            raise SasError("the exchange is not established")
        if not self._confirmed:
            raise SasError(
                "refusing to MAC before the humans have compared the string: "
                "the binding would commit to a secret nobody has checked"
            )
        return self._established.calculate_mac(key, info)

    def verify_mac(self, *, mac: str, key: str, info: str) -> bool:
        """Whether ``mac`` binds ``key`` to this exchange.

        Also refused before confirmation, for the same reason: accepting
        the other side's MAC is accepting their key, and doing that on an
        unchecked secret is the failure this protocol exists to prevent.
        """
        if self._established is None:
            raise SasError("the exchange is not established")
        if not self._confirmed:
            raise SasError("refusing to check a MAC before the humans have compared the string")
        try:
            self._established.verify_mac(key, info, mac)
        except Exception:
            return False
        return True


__all__ = [
    "EMOJI",
    "EMOJI_COUNT",
    "MAC_INFO_PREFIX",
    "SAS_INFO_PREFIX",
    "SasError",
    "ShortAuthString",
    "Verification",
    "commitment",
    "key_ids_mac_info",
    "mac_info",
    "sas_info",
    "verify_commitment",
]
