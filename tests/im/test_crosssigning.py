"""Cross-signed device verification, against signatures actually produced.

Every signature here is made by `vodozemac` and checked by `vodozemac`
through this module's own code path. A fixture of hand-written base64 would
test that this module rejects garbage and nothing else — the interesting
question is whether a chain that *is* valid verifies, and whether one that
is subtly wrong does not.

The subtly-wrong cases are the point:

* a device signed by a self-signing key that nothing vouches for — it looks
  signed, and it is signed by a key anyone could mint;
* a valid chain whose signed object is for a different device;
* a signature over the object *with* its `signatures` member, which is what
  a naive implementation signs and which never verifies against a real
  homeserver.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import vodozemac

from treble.im.crosssigning import (
    CrossSigningError,
    canonical_json,
    key_material,
    verify_device,
    verify_signed_object,
)
from treble.im.matrix import MatrixClient
from treble.im.simulator import Homeserver

USER = "@trader:treble.invalid"
DEVICE = "DESKTOP1"


class Signer:
    """An Ed25519 keypair that signs the way Matrix does."""

    def __init__(self) -> None:
        self._account = vodozemac.Account()
        self.public = self._account.ed25519_key.to_base64()

    def sign(self, payload: dict[str, Any], *, user_id: str = USER) -> dict[str, Any]:
        """Return ``payload`` with this key's signature added."""
        signature = self._account.sign(canonical_json(payload))
        signed = dict(payload)
        signatures = dict(signed.get("signatures", {}))
        by_user = dict(signatures.get(user_id, {}))
        by_user[f"ed25519:{self.public}"] = signature.to_base64()
        signatures[user_id] = by_user
        signed["signatures"] = signatures
        return signed


def _cross_signing_key(signer: Signer, usage: str) -> dict[str, Any]:
    return {
        "user_id": USER,
        "usage": [usage],
        "keys": {f"ed25519:{signer.public}": signer.public},
    }


def _device_keys(device_id: str = DEVICE) -> dict[str, Any]:
    return {
        "user_id": USER,
        "device_id": device_id,
        "algorithms": ["m.olm.v1.curve25519-aes-sha2", "m.megolm.v1.aes-sha2"],
        "keys": {f"ed25519:{device_id}": "irrelevant-for-the-chain"},
    }


@pytest.fixture
def chain() -> tuple[Signer, Signer, dict[str, Any], dict[str, Any]]:
    """A well-formed master → self-signing → device chain."""
    master, self_signing = Signer(), Signer()
    signed_self = master.sign(_cross_signing_key(self_signing, "self_signing"))
    signed_device = self_signing.sign(_device_keys())
    return master, self_signing, signed_self, signed_device


class TestAValidChain:
    def test_a_cross_signed_device_verifies(
        self, chain: tuple[Signer, Signer, dict[str, Any], dict[str, Any]]
    ) -> None:
        master, _, signed_self, signed_device = chain
        trust = verify_device(
            user_id=USER,
            device_id=DEVICE,
            master_key=master.public,
            self_signing_key=signed_self,
            device_keys=signed_device,
        )
        assert trust.verified, trust.reason

    def test_the_premise_travels_with_the_answer(
        self, chain: tuple[Signer, Signer, dict[str, Any], dict[str, Any]]
    ) -> None:
        """This module does not establish the master key — SAS or a printed
        fingerprint does, out of band. A caller that lost track of which
        root it trusted would be reporting a conclusion without its
        premise."""
        master, _, signed_self, signed_device = chain
        trust = verify_device(
            user_id=USER,
            device_id=DEVICE,
            master_key=master.public,
            self_signing_key=signed_self,
            device_keys=signed_device,
        )
        assert trust.master_key == master.public
        assert master.public[:12] in trust.summary


class TestTheChainMustHold:
    def test_a_self_signing_key_nothing_vouches_for_is_refused(self) -> None:
        """**The interesting failure.** The device is genuinely signed —
        by a key an attacker minted a moment ago. Checking only the lower
        link would call this verified."""
        attacker, self_signing = Signer(), Signer()
        unvouched = _cross_signing_key(self_signing, "self_signing")
        trust = verify_device(
            user_id=USER,
            device_id=DEVICE,
            master_key=attacker.public,
            self_signing_key=unvouched,
            device_keys=self_signing.sign(_device_keys()),
        )
        assert not trust.verified
        assert "self-signing key is not signed by the master key" in trust.reason

    def test_an_unsigned_device_is_refused(
        self, chain: tuple[Signer, Signer, dict[str, Any], dict[str, Any]]
    ) -> None:
        master, _, signed_self, _ = chain
        trust = verify_device(
            user_id=USER,
            device_id=DEVICE,
            master_key=master.public,
            self_signing_key=signed_self,
            device_keys=_device_keys(),
        )
        assert not trust.verified
        assert "not signed by the self-signing key" in trust.reason

    def test_a_device_signed_by_the_wrong_key_is_refused(
        self, chain: tuple[Signer, Signer, dict[str, Any], dict[str, Any]]
    ) -> None:
        master, _, signed_self, _ = chain
        stranger = Signer()
        trust = verify_device(
            user_id=USER,
            device_id=DEVICE,
            master_key=master.public,
            self_signing_key=signed_self,
            device_keys=stranger.sign(_device_keys()),
        )
        assert not trust.verified

    def test_a_valid_chain_for_another_device_is_refused(self) -> None:
        """The signature covers `device_id`, so a mismatch means the caller
        asked about a different device than the one it handed over.
        Answering yes would attach a valid chain to the wrong device."""
        master, self_signing = Signer(), Signer()
        signed_self = master.sign(_cross_signing_key(self_signing, "self_signing"))
        other = self_signing.sign(_device_keys("LAPTOP2"))
        trust = verify_device(
            user_id=USER,
            device_id=DEVICE,
            master_key=master.public,
            self_signing_key=signed_self,
            device_keys=other,
        )
        assert not trust.verified
        assert "LAPTOP2" in trust.reason

    def test_a_signature_from_another_user_does_not_count(
        self, chain: tuple[Signer, Signer, dict[str, Any], dict[str, Any]]
    ) -> None:
        """Signatures are keyed by user. One valid for someone else must
        not verify here, or a chain could be borrowed wholesale."""
        master, self_signing, signed_self, _ = chain
        for_other = self_signing.sign(_device_keys(), user_id="@other:treble.invalid")
        trust = verify_device(
            user_id=USER,
            device_id=DEVICE,
            master_key=master.public,
            self_signing_key=signed_self,
            device_keys=for_other,
        )
        assert not trust.verified


class TestCanonicalJson:
    def test_the_signatures_member_is_excluded(self) -> None:
        """An object cannot contain its own signature. Signing the object
        *with* it is what a naive implementation does, and it never
        verifies against a real homeserver."""
        payload = {"a": 1, "signatures": {"x": "y"}, "unsigned": {"age": 3}}
        assert json.loads(canonical_json(payload)) == {"a": 1}

    def test_keys_are_sorted_and_whitespace_is_absent(self) -> None:
        assert canonical_json({"b": 1, "a": 2}) == b'{"a":2,"b":1}'

    def test_non_ascii_is_signed_as_utf8_not_escaped(self) -> None:
        """`ensure_ascii` at its default would sign `\\uXXXX` escapes. The
        two produce different signatures and only one verifies — the same
        class of mistake the web renderer made with the layout goldens."""
        assert canonical_json({"name": "Zürich"}) == '{"name":"Zürich"}'.encode()

    def test_it_is_not_the_render_canonical_json(self) -> None:
        """Similar name, incompatible rules: the render one indents for a
        readable golden, which is fatal here. Asserted because reusing it
        would look like sensible deduplication."""
        from treble.render.contract.buffer import canonical_json as render_json

        payload = {"b": 1, "a": 2}
        assert render_json(payload) != canonical_json(payload).decode()

    def test_a_round_trip_signature_over_canonical_json_verifies(self) -> None:
        """End to end through the real primitive: sign the canonical bytes,
        verify through this module's own path."""
        signer = Signer()
        payload = signer.sign({"user_id": USER, "keys": {"a": "b"}})
        assert verify_signed_object(payload, user_id=USER, signing_key=signer.public)


class TestKeyMaterial:
    def test_the_single_key_is_returned(self) -> None:
        signer = Signer()
        assert key_material(_cross_signing_key(signer, "master")) == signer.public

    def test_two_keys_are_refused_rather_than_picked(self) -> None:
        """Choosing one would make the answer depend on dict ordering."""
        with pytest.raises(CrossSigningError, match="exactly one key"):
            key_material({"keys": {"ed25519:a": "a", "ed25519:b": "b"}})

    def test_no_keys_is_refused(self) -> None:
        with pytest.raises(CrossSigningError, match="exactly one key"):
            key_material({})


class TestMalformedInput:
    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"signatures": "not a dict"},
            {"signatures": {USER: "not a dict"}},
            {"signatures": {USER: {"ed25519:k": 42}}},
        ],
    )
    def test_a_missing_or_malformed_signature_is_false_not_an_exception(
        self, payload: dict[str, Any]
    ) -> None:
        """A caller asking "is this signed" wants an answer. Absent and
        invalid mean the same thing to it, and raising would make the happy
        path carry a try block."""
        assert verify_signed_object(payload, user_id=USER, signing_key="k") is False

    def test_a_malformed_public_key_is_false(self) -> None:
        signer = Signer()
        payload = signer.sign({"a": 1})
        assert verify_signed_object(payload, user_id=USER, signing_key="not-base64!") is False


class TestThroughTheClient:
    """The chain fetched from a homeserver and checked end to end.

    The verifier is only worth having if something calls it with real
    keys. These publish a chain on the simulator, ask the client, and
    check the answer — including the case a homeserver can create by
    publishing nothing, which must read as "no cross-signing" rather than
    as "not verified".
    """

    def _published(self) -> tuple[Homeserver, Signer]:
        server = Homeserver()
        server.accounts["trader"] = "hunter2"
        master, self_signing = Signer(), Signer()
        server.master_keys[USER] = master.sign(_cross_signing_key(master, "master"))
        server.self_signing_keys[USER] = master.sign(
            _cross_signing_key(self_signing, "self_signing")
        )
        server.device_keys[USER] = {DEVICE: self_signing.sign(_device_keys())}
        return server, master

    def _client(self, server: Homeserver) -> MatrixClient:
        client = MatrixClient(transport=server.transport)
        client.login(user="trader", password="hunter2")  # noqa: S106
        return client

    def test_a_published_chain_verifies_through_the_client(self) -> None:
        server, master = self._published()
        trust = self._client(server).device_trust(USER, DEVICE)
        assert trust.verified, trust.reason
        assert trust.master_key == master.public

    def test_an_unknown_device_is_not_verified(self) -> None:
        server, _ = self._published()
        trust = self._client(server).device_trust(USER, "PHONE9")
        assert not trust.verified
        assert "no keys for device" in trust.reason

    def test_a_user_with_no_cross_signing_says_so(self) -> None:
        """Distinct from "not verified": nothing was published, so there is
        no chain to fail. A client that reported these the same way would
        tell a reader a device was rejected when it was never claimed."""
        server = Homeserver()
        server.accounts["trader"] = "hunter2"
        trust = self._client(server).device_trust(USER, DEVICE)
        assert not trust.verified
        assert "published no cross-signing keys" in trust.reason

    def test_the_homeservers_own_key_is_the_premise_and_it_shows(self) -> None:
        """**A stated weakness.** The master key comes from the homeserver,
        so a server that wanted to lie could serve its own chain and this
        would verify. Closing it needs the key confirmed out of band. Until
        then the premise travels with the answer, and this asserts that it
        does — a boolean alone would hide exactly this."""
        server, master = self._published()
        trust = self._client(server).device_trust(USER, DEVICE)
        assert trust.verified
        assert trust.master_key == master.public, "the premise must be visible"
        assert master.public[:12] in trust.summary
