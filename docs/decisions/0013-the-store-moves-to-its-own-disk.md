# 0013 — The store moves to its own disk, and says so when it is not there

**Status:** accepted
**Date:** 2026-09-01

## Context

The data directory grows and the disk does not. Measured on this install:
**8.8 GB/yr** at the cadences the sources declare, against **8.5 GB free** on
a 228 GB volume that is 97% full — and 97% of that volume is not this
project. `treble storage` puts the runway at about a year.

Every lever inside the repository has now been pulled, and each was worth
pulling on its own terms:

| | saving |
|---|---|
| payload gzip (2026-08-19) | 1.774 GB → 0.594 GB |
| write-path coalescing (2026-08-30) | 95.6% of every refresh |
| GLEIF RR deltas (2026-09-01) | 13.60 → 0.07 GB/yr |
| gleif-isin weekly (2026-09-01) | 9.72 → 1.39 GB/yr |

Together they took the projection from 24.15 to 8.8 GB/yr. None of them
changes the shape of the problem: the payload store is the substrate I5
replays from, it is append-only by construction, and a workstation that
keeps twenty years of point-in-time data will need twenty years of disk.

The remaining question is not how to store less. It is **where**.

## Decision

**The data directory moves to a disk of its own, by one command, and the
workstation follows it without being configured.**

`treble relocate <path>` copies, verifies, and only then removes the
original. Verification is not a file count: every payload is read back at
the target through `PayloadStore.get`, which checks it against its content
address, and the fact counts are compared across both stores. A truncated
or dropped file is caught during the move rather than the next time
somebody asks for a price.

### Nothing is deleted before the copy is proved

An interruption at any point leaves the original intact and the target
incomplete, which is the safe way round. The payloads are the reason: the
derived tables can be rebuilt by replaying them, and nothing can rebuild
the payloads, because they are the bytes a source served on a day that has
passed.

### The old directory is left with a pointer, not silence

`RELOCATED.json` names the new location and `cmd.paths` follows it, through
chains, refusing loops. So relocation needs **no configuration at all** —
nothing to export, nothing to remember.

That is not convenience. `paths.default_data_dir` exists because a relative
data path once meant launching from the wrong directory "silently created a
fresh empty store and rendered a screen of honest-looking dashes with no
error". A store held in place by an environment variable reproduces that
failure exactly, the first time anyone opens a shell without it.

### An absent store is refused, never recreated

`core.datadir.verify` runs before anything is created. If the pointer leads
somewhere that is not there, the command stops with the sentence that
matters — *"the store was moved to /Volumes/…; if it is on an external
volume, is that volume mounted?"* — and exit code **2**, distinct from 1 so
a script can tell "the disk is not attached" from "the command failed".

Proved against a real volume rather than a simulation: an APFS image was
created, mounted, relocated onto, **detached**, and the guard fired; then
reattached, and all payloads verified against their content addresses.

### A directory named outright is not a relocation

`verify` takes the relocation branch only when a pointer chain actually
ends at the directory in question. `--data-dir /somewhere/new` on a first
run is a caller naming a path, not following a signpost, and telling them
their store "was moved" somewhere they just typed would be the guard
inventing a history. The CLI suite caught this by going red on every
command that passes a temporary directory.

## Consequences

- `treble relocate` is the answer to the runway warning `treble storage`
  prints, and the two reference each other.
- `core/datadir.py` sits in `core`, not `store`: every layer must agree
  where the data directory is, `render.server` reads it, and I7 forbids
  presentation code from importing `treble.store`. The import contract
  caught the violation the moment it was introduced.
- **One disk is capacity, not safety.** `--keep-original` copies without
  removing, because the payloads are the only part of the store that cannot
  be rebuilt from anything else. This ADR does not decide a backup policy;
  it makes a second verified copy one command.

## What was not chosen

**Cloud object storage.** It is a recurring cost against a standing
zero-cost constraint, and some of what the store holds is
redistribution-restricted (Twelve Data's free tier is personal use only and
forbids sharing), so uploading it would need a licence review this project
does not need to have.

**A retention policy that deletes old payloads.** They are what I5 replays
from. Deleting them buys a year and costs the ability to reconstruct any
state before the deletion, which is the property the whole storage design
exists to provide.
