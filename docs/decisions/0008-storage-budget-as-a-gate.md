# 0008 — The storage budget is a gate, not a cleanup script

**Status:** accepted
**Date:** 2026-08-25
**Supersedes nothing. Extends:** [0007 — cold tier Parquet, hot DuckDB](0007-cold-tier-parquet-hot-duckdb.md)

## Context

The data directory reached 1.7 GB on a 245 GB laptop that was 96% full, and
the workstation was blamed for the machine being slow. It was not the cause —
the project was 3.6 GB of 211 GB in use, 1.7%, and 52% of RAM was free with no
Python process running — but 1,007 MB of what it held was waste:

| | |
|---|---|
| `treble.db` | 335.7 MB holding 0.76 MB of facts — 937 used blocks of 1343 |
| `treble.db.bak-20260825-090507` | 336 MB, hand-made, 12 hours stale |
| `treble.db.bak-20260825-213650` | 336 MB, hand-made, 8 hours stale |

**Nothing was broken.** `treble compact` was correct, tested, hash-verified,
crash-safe, and measured at 21.7x — 1205.4 MB to 55.6 MB across 12.7 million
facts. It was also *manual*, so nothing ran it, and the hot tier regrew after
every ingest and stayed there. The backups were made by hand before a risky
migration by a session that had no mechanism telling it when to remove them.

This is a class of defect the three existing structural gates cannot see. They
check code: a module with no tests, a field with no reader, a module nothing
imports. None of them look at the working copy, and a correct routine that
nobody runs is worth exactly what one that does not exist is worth.

## Decision

Add a fourth structural gate over the *data directory*, with the thresholds in
a pure function so the failure path can be tested.

1. **`treble/store/storage.py`** — `measure()` walks the directory and reports
   every component with its size and, where a documented lossless command
   would return bytes, its waste and the command's name. `verdict()` is pure:
   a report and a budget in, a pass or fail with reasons out.
2. **`scripts/check_storage_budget.py`** — a `make gate` stage. Fails when
   reclaimable waste exceeds 256 MB, or when a partial compaction file exists
   at any size.
3. **`treble storage`** — the same measurement as a command, with `--fix`.
4. **Automatic compaction** — `refresh` compacts when the hot tier exceeds
   1,000,000 rows. The command that grows the store cleans up after it.

### Why a byte budget and not a percentage

The waste was 1,007 MB beside 668 MB of real data — waste *larger* than the
payload. No percentage threshold flags that as unusual, because the ratio is
only strange if you already know which side is which. 256 MB is roughly one
uncompacted ingest cycle: below it, compaction is not worth the Parquet
rewrite; above it, the store is carrying a full cycle it never cleaned up.

### Why the threshold is 1,000,000 hot rows

The hot tier was 1,953,485 rows when the store hit 336 MB. A threshold above
that would have watched the whole thing happen. It is asserted in
`test_the_default_would_have_fired_before_the_incident`.

### Why `payloads/` is never waste

`data/payloads` is 604 MB and the largest component, and it is the one
directory that must never be offered for deletion. It holds the
content-addressed source bytes every fact was derived from. The database is
derived; the payloads are not, and no source will serve its 2024 bytes again
once it has moved on. `test_payloads_are_never_waste` pins this.

## The gate skips in CI, loudly

A fresh checkout has no `data/`, so the skip path is the one that runs almost
everywhere. It prints what it skipped and why. This repository has already
shipped a guard whose condition never matched — the temp-file filter in
`cold.py` that `*.parquet` had already excluded — and the lesson recorded was
to make the inert case loud rather than let silence read as a pass.

The consequence is honest and worth stating: **in CI this gate proves nothing.**
It is a local check. What makes it trustworthy is not that it runs in CI but
that `verdict()` is pure and its failure path is exercised by tests that
construct the incident — 336 MB twice, over a 256 MB budget — and assert the
check returns not-ok. The script's three paths were also driven end to end by
hand: pass on the real directory, loud skip on an absent one, exit 1 on the
reconstructed incident.

## Consequences

- A commit cannot go in while the working store carries more than 256 MB of
  reclaimable waste. `TREBLE_WASTE_LIMIT_BYTES` overrides it for a machine
  with different headroom; a value that does not parse is an error rather
  than a silent fallback to the default.
- `refresh` is slower when the hot tier is large — 19.7s for two million
  rows, once, rather than never.
- Compaction failure inside `refresh` is reported and swallowed. It is
  crash-safe by construction, so the worst case is a store that stays large,
  which is what this improves rather than something it breaks. Aborting a
  successful ingest because the optional tidy-up failed would lose real work.

## What this does not fix

`treble.db` is derived and could in principle be rebuilt from `data/payloads`
and `data/ingest.db` — that is what I5 is for, and it is why the backups were
the wrong thing to protect. **There is no command that does it.** No `treble
replay`, no `treble rebuild`. The property is architectural: deterministic
parsers, content-addressed payloads, provenance ids reproducible from their
inputs. It has never been executed end to end.

Until it has, "the database is disposable" is an explanation, not a tested
fact — the failure mode this repository names E, and the reason it is written
here as an open item rather than assumed by the code above. The storage gate
does not depend on it: nothing in this ADR deletes a database. But the case
for keeping 604 MB of payloads *does* depend on it, and that case is currently
argued rather than demonstrated.
