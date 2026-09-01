# Where the data lives

The workstation keeps everything in one **data directory**. This is how to
move it to a disk of its own, and what happens when that disk is not there.

## The short version

```bash
treble relocate /Volumes/YourDisk/treble-data --dry-run   # check
treble relocate /Volumes/YourDisk/treble-data             # do it
```

Then nothing. No variable to set, no path to remember: the old directory is
left with a pointer and the workstation follows it.

## What is in the data directory

| | what it is | replaceable? |
|---|---|---|
| `payloads/` | the exact bytes each source served, content-addressed | **no** |
| `treble.db` | facts parsed from those payloads | yes — replay |
| `cold/` | settled facts in Parquet | yes — replay |
| `ingest.db` | append-only log of every fetch | **no** |
| `vault/` | WORM archive | **no** |

The two marked **no** are why a move verifies rather than trusting `cp`.
A payload is the bytes a source served on a day that has passed; if it is
lost, the facts parsed from it can never be reproduced (I5). Everything
marked *yes* can be rebuilt with `treble replay`.

## How much space to plan for

`treble storage` prints the projection and the runway:

```
Projected 24.6 MB/day (8.8 GB/yr) at the declared cadences, against 8.3 GB
free — 343 days.
    gleif-isin: 3.6 MB/day
    gleif-rr: 19.4 MB/day
```

It is measured from what has actually been fetched — the five most recent
payloads per source, times the cadence that source is pulled on — so it
follows a change in strategy rather than averaging it away.

**Roughly 9 GB a year**, and it will rise if update frequency does. A 1 TB
disk is a century at that rate; 2 TB is the size to buy if you would rather
not think about it again. An external SSD is worth it over a spinning disk
for one reason: `make gate` reads the store, and the test suite's speed is
what keeps the project pleasant to work on.

## What happens when the disk is not attached

Every command stops before touching anything:

```
the store was moved to /Volumes/YourDisk/treble-data, and nothing is there.
  If it is on an external volume, is that volume mounted?
  The pointer is /Users/you/dev/treble-tracker/data/RELOCATED.json —
  delete it only if you have moved the store back.
```

Exit code **2**, distinct from 1, so a scheduled job can tell "the disk is
not attached" from "the command failed".

It does **not** create an empty store and carry on. That is the failure this
is built to prevent, and the repository has met it once already: a relative
data path meant launching from the wrong directory silently created a fresh
store and rendered a screen of honest-looking dashes with no error.

## Safety: one disk is capacity, not a backup

A single external disk holds the only copy of the payloads. It can die.

```bash
treble relocate /Volumes/Mirror/treble-data --keep-original
```

copies and verifies without removing the source, which gives two verified
copies. Time Machine including the volume works too — the store is ordinary
files, and the content-addressed payloads make a partial restore detectable
rather than silent.

What is already safe without any of this:

- **The code** is on GitHub.
- **The derived tables** rebuild from payloads with `treble replay`.

So the thing to protect is `payloads/` and `ingest.db`. Everything else is
convenience.

## Moving it back, or somewhere else again

`treble relocate` from wherever it currently is. Chains are followed, so a
store moved three times is still found from the original path. To detach a
store from its pointer entirely, delete `RELOCATED.json` — but only once
the store is actually back where that directory is.

## If something looks wrong

- `treble storage` — what is on disk, what is reclaimable, how long it lasts
- `treble status` — whether each source is still flowing
- `.treble-store.json` in the data directory — which store this is, readable
  with `cat`
