# 0010 — What `parse` reads beyond its payload is recorded, or derived

**Status:** accepted
**Date:** 2026-08-25
**Fixes the finding in:** [0009 — replay rebuilds the store](0009-replay-rebuilds-the-store.md)

## Context

Replaying the whole ingest log found three adapters whose `parse` read
configuration passed to `__init__`. The log recorded which bytes arrived and
which parser version applied, and that was not enough: the same payload and the
same parser produced different facts depending on arguments nobody wrote down.

| adapter | read | a replay without it |
|---|---|---|
| `edgar-companyfacts` | `_accepted` | knowledge dates degrade to filed end-of-day |
| `edgar-bulk` | `_ciks` | **superset** — every filer in the archive, 7.4M facts |
| `gleif-isin` | `_isins` | **zero facts** from a payload holding 9.1M rows |

I5 says replaying the log reconstructs any past state exactly. For these three
it did not, and nothing said so.

## Decision

Two mechanisms, because the three cases are not the same problem.

### Derive it, where the data is already stored

`edgar-companyfacts` needs accession → acceptance time. That mapping is a pure
function of **`edgar-submissions` payloads**, which the payload store already
holds — `Populator._accepted_times` was already building it that way, by reading
payloads back out of the log rather than refetching.

So the adapter derives it: `_acceptance_times` prefers the injected mapping
when `Populator` supplies one, and otherwise reconstructs it from the log by
matching the CIK in `source_uri`. Nothing is recorded because nothing needs to
be.

**This is why it matters which mechanism you pick.** Recording would have
looked like a fix and left all 108 existing entries degraded, because their
configuration was never captured. Deriving fixes them retroactively — measured:
**306,031 facts replayed, 0 not already in the live store**, on entries written
long before any of this existed.

### Record it, where it cannot be derived

`edgar-bulk`'s CIK filter and `gleif-isin`'s ISIN filter come from the universe
config and the store. Neither is in the payload or the URI, so there is nothing
to derive from and recording is the only option.

- `IngestLog` gains a nullable `parse_config VARCHAR` holding JSON, applied by
  an `ADD COLUMN IF NOT EXISTS` migration that runs on every open.
- `SourceAdapter.parse_config()` returns what `parse` reads beyond the payload,
  `{}` by default; `apply_parse_config()` restores it. `run()` records it,
  `replay_source` applies it per entry.
- `replay.needs_config(cls)` reports whether an adapter overrides
  `parse_config`, detected by the override rather than by calling it — a
  `parse_only` instance has no `__init__` state, so calling it would raise
  exactly where the question is being asked.

### Nullable, deliberately

`{}` and `None` are different answers. The first says the adapter needed
nothing; the second says nobody asked. A `NOT NULL DEFAULT '{}'` would make
those indistinguishable — which is precisely the ambiguity that let this run
unnoticed. `SourceReplay.unconfigured` counts entries replayed without
configuration their parse needed, and `treble replay` prints them in yellow:
not an error, and not a success either.

## Consequences

- Ingests made from now on replay faithfully for all nineteen adapters.
- The 5 `edgar-bulk` and 2 `gleif-isin` entries already in the log stay
  unreplayable and are **reported as such** every time. They are not
  recoverable: the filters were never written down, and reconstructing them
  from the store would be deriving the input from the output.
- `parse_config` must round-trip through JSON. A `frozenset[int]` that comes
  back as a list of strings would filter nothing, so the round trip is tested
  rather than assumed.
- Overriding `parse_config` without `apply_parse_config` produces a log that
  records configuration replay then ignores — worse than not recording it,
  because it looks reproducible. Both are stated in the base class docstring;
  neither is enforced by a type.

## What is still not proved

Unchanged from ADR-0009: `rebuild` re-derives adapter facts, not the entity
graph or the security master that run over them. A full reconstruction is
replay plus those steps, and the second half has not been measured.
