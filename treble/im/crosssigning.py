"""Cross-signed device verification — what Matrix means by `verified` (P3_1).

`im/identity.py` proves *domain control*: a homeserver authenticated an MXID,
so whoever holds it controls that account on that domain. That is genuinely
stronger than an email From header, and it is **not** what Matrix means when
it marks a device verified.

Matrix means a signature chain:

    master key  ──signs──▶  self-signing key  ──signs──▶  device key

The master key is the user's root of trust. The self-signing key signs their
own devices; a separate user-signing key signs *other* users' master keys, and
is not needed to answer "is this device this user's". So a device is verified
for a user when its key carries a valid signature from that user's
self-signing key, and that self-signing key carries a valid signature from
their master key.

Nothing here establishes that a master key is *the right one* — that is what
SAS verification or a printed fingerprint does, out of band, and this module
takes the master key as the caller's premise rather than pretending to
discover it. `verify_device` says which premise it used, so a caller cannot
lose track of what was assumed.

## Canonical JSON is where this goes wrong

Matrix signs the canonical serialisation of an object with its `signatures`
and `unsigned` members removed: UTF-8, keys sorted, no insignificant
whitespace. A signature computed over anything else is not wrong-looking, it
simply never verifies, and the failure reads as a bad key.

**`render.contract.buffer.canonical_json` is not this and must not be reused.**
It serialises with `indent=2`, which is right for a readable golden and fatal
here. The two names are similar enough that sharing them would look like
sensible reuse, which is exactly why this one is defined locally and says so.

## vodozemac, not olm (ADR-0011)

Measured 2026-08-30: Homebrew no longer carries `libolm`, and `python-olm`
fails to build because it compiles a bundled copy (`make static`, exit 2).
`vodozemac` is the Matrix project's own Rust reimplementation, ships wheels
for macOS and Linux, and needs no system library. It supplies
`Ed25519PublicKey.verify_signature`, which is the whole of what this module
needs. That call takes bytes and *raises* on a bad signature rather than
returning False, which is why the wrapper below converts it — a verifier
whose failure path is an exception invites a caller to forget the try.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import vodozemac

from treble.im.canonical import UNSIGNED_MEMBERS, signing_bytes

#: The algorithm prefix on a cross-signing or device key id.
ED25519 = "ed25519"

#: Key usages, as the `/keys/device_signing/upload` payload names them.
MASTER = "master"
SELF_SIGNING = "self_signing"
USER_SIGNING = "user_signing"


class CrossSigningError(ValueError):
    """A signature chain could not be established, and why."""


def canonical_json(payload: dict[str, Any]) -> bytes:
    """Matrix canonical JSON of ``payload``, minus the unsigned members.

    Delegates to `im.canonical.signing_bytes`: the serialisation is shared
    with `im.sas`, which needs the same canonical form for a commitment
    hash but must *not* strip anything. Two copies of these separator and
    `ensure_ascii` choices would not look wrong anywhere, and would make
    every signature disagree with every other client.
    """
    return signing_bytes(payload)


def _signature(payload: dict[str, Any], *, user_id: str, key_id: str) -> str | None:
    signatures = payload.get("signatures")
    if not isinstance(signatures, dict):
        return None
    by_user = signatures.get(user_id)
    if not isinstance(by_user, dict):
        return None
    value = by_user.get(key_id)
    return value if isinstance(value, str) else None


def verify_signed_object(payload: dict[str, Any], *, user_id: str, signing_key: str) -> bool:
    """Whether ``payload`` carries a valid signature from ``signing_key``.

    ``signing_key`` is the base64 public key; the signature is looked up
    under `ed25519:<key>`, which is how Matrix names a cross-signing
    signature — the key *is* its own id, unlike a device signature where
    the id is the device.

    Returns False rather than raising for a missing or malformed signature.
    A caller asking "is this signed" wants an answer, and the distinction
    between absent and invalid is not one a verifier should collapse into
    an exception the happy path has to catch.
    """
    encoded = _signature(payload, user_id=user_id, key_id=f"{ED25519}:{signing_key}")
    if encoded is None:
        return False
    try:
        key = vodozemac.Ed25519PublicKey.from_base64(signing_key)
        key.verify_signature(
            canonical_json(payload), vodozemac.Ed25519Signature.from_base64(encoded)
        )
    except Exception:
        # vodozemac raises for a malformed key, a malformed signature and a
        # signature that does not verify. All three mean the same thing to
        # a caller — this is not signed by that key — and telling them
        # apart here would invite a caller to treat one as recoverable.
        return False
    return True


def key_material(keys: dict[str, Any]) -> str:
    """The single public key out of a cross-signing key object.

    A cross-signing key carries exactly one key under `keys`, mapped from
    `ed25519:<key>` to the same value. Refuses anything else rather than
    picking the first: an object with two keys is not one this module
    understands, and choosing one would make the answer depend on dict
    ordering.
    """
    material = keys.get("keys")
    if not isinstance(material, dict) or len(material) != 1:
        raise CrossSigningError(
            f"a cross-signing key must carry exactly one key, found "
            f"{len(material) if isinstance(material, dict) else 'none'}"
        )
    value = next(iter(material.values()))
    if not isinstance(value, str):
        raise CrossSigningError("cross-signing key material is not a string")
    return value


@dataclass(frozen=True)
class DeviceTrust:
    """Whether a device is cross-signed, and on what premise."""

    user_id: str
    device_id: str
    verified: bool
    #: The master key the answer rests on. Published because this module
    #: does not establish it — SAS or a printed fingerprint does, out of
    #: band — and a caller that lost track of which root it trusted would
    #: be reporting a conclusion without its premise.
    master_key: str
    #: Why not, when not. Empty when verified.
    reason: str = ""

    @property
    def summary(self) -> str:
        if self.verified:
            return f"cross-signed by {self.master_key[:12]}…"
        return f"not cross-signed: {self.reason}"


def verify_device(
    *,
    user_id: str,
    device_id: str,
    master_key: str,
    self_signing_key: dict[str, Any],
    device_keys: dict[str, Any],
) -> DeviceTrust:
    """Check master → self-signing → device for one device.

    Both links are required and are checked in that order, so a failure
    names the link that broke rather than the chain. A device signed by a
    self-signing key that nothing vouches for is the interesting case: it
    looks signed, and it is signed by a key an attacker could have minted.
    """

    def trust(ok: bool, reason: str = "") -> DeviceTrust:
        return DeviceTrust(
            user_id=user_id,
            device_id=device_id,
            verified=ok,
            master_key=master_key,
            reason=reason,
        )

    if not verify_signed_object(self_signing_key, user_id=user_id, signing_key=master_key):
        return trust(False, "the self-signing key is not signed by the master key")

    try:
        signing = key_material(self_signing_key)
    except CrossSigningError as exc:
        return trust(False, str(exc))

    if not verify_signed_object(device_keys, user_id=user_id, signing_key=signing):
        return trust(False, "the device is not signed by the self-signing key")

    claimed = device_keys.get("device_id")
    if claimed != device_id:
        # The signature covers the whole object including `device_id`, so a
        # mismatch means the caller asked about a different device than the
        # one it handed over — answering yes would attach a valid chain to
        # the wrong device.
        return trust(False, f"the signed object is for device {claimed!r}, not {device_id!r}")

    return trust(True)


__all__ = [
    "ED25519",
    "MASTER",
    "SELF_SIGNING",
    "UNSIGNED_MEMBERS",
    "USER_SIGNING",
    "CrossSigningError",
    "DeviceTrust",
    "canonical_json",
    "key_material",
    "verify_device",
    "verify_signed_object",
]
