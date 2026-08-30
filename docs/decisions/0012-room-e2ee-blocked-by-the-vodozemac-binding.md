# 0012 — Room E2EE is blocked by the vodozemac Python binding

**Status:** accepted
**Date:** 2026-08-30
**Follows:** [0011 — vodozemac, not olm](0011-vodozemac-not-olm-for-matrix-e2ee.md)

## Context

ADR-0011 established that `vodozemac` is what Matrix cryptography should be
built on here, and that cross-signing and E2EE were never blocked on an
environment question. Cross-signing and SAS were then built on it and work.

E2EE was picked up next. Matrix room encryption is two layers:

    room message ──Megolm──▶ m.room.encrypted
    Megolm key   ──Olm────▶ m.room_key, one per recipient device

Both are needed. Megolm alone, with the session key distributed in
plaintext, puts the key in the homeserver's hands while the client claims
the room is encrypted — the failure `im/matrix.py` has warned about since it
was written.

## What was measured

On 2026-08-30, against `vodozemac==0.10.0`, the latest release:

| operation | result |
|---|---|
| Olm session establish, encrypt, decrypt | **works** |
| Olm message → wire form → back (`AnyOlmMessage.from_parts`) | **works** |
| Megolm encrypt / decrypt in-process | **works** |
| `SessionKey.from_base64` | **does not exist** |
| `ExportedSessionKey.from_base64` | **does not exist** |
| `InboundGroupSession(<base64 str>)` | rejected — needs a `SessionKey` object |

Both key types expose `to_base64` and neither exposes its inverse.
`InboundGroupSession` accepts only those objects, so there is no path from
the base64 an `m.room_key` event carries back to a usable session. `uv`
reports no version above 0.10.0.

## Decision

**Ship the Olm layer. Refuse room-key import, in code, by name.**

`RoomDecryptor.import_room_key` raises `EncryptionError` naming the missing
constructor, the consequence, and the fact that Olm is unaffected. It is a
raise rather than a documented caveat for two reasons: a documented
limitation is one a caller discovers after shipping, and a silent fallback
would produce a device that believed it had joined an encrypted room and
simply never decrypted anything.

`RoomDecryptor.accept_session` takes a live `SessionKey` object, so Megolm
is exercised and correct in-process. That is not a substitute for
distribution and does not pretend to be — an object cannot cross a socket,
which is exactly the problem.

## Why this is disqualifying rather than partial

The failure is **asymmetric**. This package can *send* a room key another
client would import, and can never *receive* one. So a Treble device could
encrypt a room nobody here could read, and could not read a room anyone
else encrypts. A client that can only talk is not a participant.

Claiming room encryption on that basis would be worse than claiming none.

## What works, and is not diminished by this

**Olm.** Device-to-device encryption round trips through the Matrix wire
form completely: pre-key and normal messages, session establishment from a
claimed one-time key, `{"type": 0|1, "body": base64}` on the wire. That is
what carries `m.room_key`, key-verification events, and everything else
addressed to a device rather than a room — so the channel SAS runs over is
now encryptable even though rooms are not.

## Consequences

- `IM` continues to say on screen that there is no room encryption, and
  that statement is now *true for a measured reason* rather than because
  nothing was built.
- The refusal is pinned by a test asserting the binding still lacks
  `from_base64` on both key types. The day it gains one, that test fails,
  the exception is deleted, and room E2EE is four lines away.
- P3_1 does not reach 1.0 on this. It should not: the gate criterion is
  E2EE, and half of it is unavailable.

## What was not done, and why

No pickle-based transport. `GroupSession.pickle` would move a session
between processes, and it is not the Matrix wire format — a room key
distributed that way interoperates with no Matrix client. That is the same
interop failure mutation testing caught three times in the SAS work
(a sorted emoji list, padded base64, a dropped `KEY_IDS` marker), and
choosing it deliberately after finding it accidentally three times would be
hard to defend.

No vendored Rust binding. Building one is a real option and a large one,
and it is a decision about maintenance burden rather than about this
feature.
