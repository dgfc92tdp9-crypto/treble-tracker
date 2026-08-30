# 0011 — Matrix E2EE and cross-signing use vodozemac, not olm

**Status:** accepted
**Date:** 2026-08-30
**Settles the open question in:** P3_1 (`config/completion.yaml`), which has said
since it was written that the dependency choice here was "an ADR question before
a coding one" and that the question was still open.

## Context

P3_1 sits at 0.7 with three outstanding items:

1. Synapse unrun — `deploy/synapse/compose.yaml` shipped and has never been
   executed, because Docker is not installed on this machine;
2. no cross-signed device verification;
3. no E2EE.

The ledger recorded all three as blocked on new environment dependencies, and
grouped 2 and 3 under "olm" — the C library Matrix clients have historically
used for the Double Ratchet and Megolm. That grouping turns out to be wrong,
and the correction is the point of this record.

## What was measured

Three probes on this machine, 2026-08-30:

| probe | result |
|---|---|
| `brew info libolm` | **no such formula.** Homebrew no longer carries it |
| `uv pip install python-olm` | **build fails.** It compiles bundled libolm; `make static` exits 2 |
| `uv pip install vodozemac` | **installs from a wheel**, 0.10.0, no system library |

`vodozemac` imports and exports exactly the primitives both outstanding items
need:

- `Account` — device identity keys
- `Curve25519PublicKey` / `Curve25519SecretKey` — Olm key agreement
- `GroupSession` / `InboundGroupSession` — Megolm, which is what encrypts a room
- `Ed25519PublicKey` / `Ed25519Signature` — **cross-signing signatures**
- `EstablishedSas` — the interactive device-verification flow

## Decision

**Use `vodozemac` when this work is built. Do not use `python-olm`.**

vodozemac is the Matrix project's own Rust reimplementation of the same
protocols, and it is what the ecosystem moved to. It ships prebuilt wheels, so
it adds a Python dependency and *no* system dependency — no compiler, no
Homebrew formula, no Docker.

**Do not add the dependency until something uses it.** This repository has
found four mechanisms declared and switched off, and an unused entry in
`pyproject.toml` is the same defect in a new place. `vodozemac` goes in with
the commit that first calls it.

## Consequences, and a correction to the ledger

**Items 2 and 3 were never blocked on an unanswered environment question.**
They were blocked on a library that had been deprecated out from under the
assumption, and nobody had checked. Cross-signing and E2EE are ordinary coding
work whenever they are picked up, and P3_1's basis now says so.

**Item 1 is still genuinely blocked, and it is a smaller blocker than it was.**
Synapse needs Docker; Docker is not installed; this machine is at 97% disk with
7.3 GB free, so installing Docker Desktop and a Synapse image is not a decision
to take casually and is not one to take without the operator. But since
ADR-0011's sibling work — `im/server.py`, serving the in-repo homeserver over
real HTTP — the client is exercised over a socket rather than only against an
in-process dict, so the marginal value of Synapse is now *federation and
someone else's implementation*, not "is this client wired up at all".

## What this does not settle

Whether E2EE should be built at all before §22.1's entitlement model exists.
IM currently states on screen that there is no encryption, which is honest; an
encryption claim over a plaintext transport is the worst thing that module
could be wrong about, and half-built E2EE is a way to make exactly that claim.
The order matters and this record does not decide it — it decides only that
when the work happens, `vodozemac` is what it is built on.

Nor does it change what an identity claim means. `im/identity.py` proves domain
control through a whoami round trip; against a homeserver you start yourself
that proves the transport and nothing about who anybody is. Cross-signing is
what would make `verified` mean what Matrix means by it, and that is the first
piece worth building of the three.
