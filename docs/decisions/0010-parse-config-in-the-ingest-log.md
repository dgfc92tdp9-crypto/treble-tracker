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

## The full replay, measured

All 488 payloads, all nineteen adapters, into an empty store: **13,845,567
facts in 360s with no network**, against a live store of 14,525,794.

| | facts | |
|---|---|---|
| bit-identical, all twelve columns | 3,639,527 | 9 sources |
| identical content, hash-equal, provenance renamed | 8,066,887 | `edgar`, `ecb-fx`, `treasury-auctions` |
| reproduced inside a diverged source | 812,381 | `gleif-rr`, `sec-nport`, `dtcc-sdr` |
| **total live facts reproduced** | **12,518,795** | **86.2%** |

The 2,006,999 not reproduced decompose completely:

| cause | facts | should replay reproduce it? |
|---|---|---|
| `gleif-rr` superseded two-fact encoding (1,326,084 `:status` + 663,380 bare) | 1,989,464 | No — the parser was re-keyed to `:state` |
| `sec-nport` pre-currency-fix output | 14,915 | No — the parser was corrected |
| `dtcc-sdr` report-window change | 274 | No — same |
| `gleif-isin`, replayed unconfigured | 2,346 | **Yes, and it cannot** |

**2,004,653 of 2,006,999 are superseded parser output**, which I2 keeps and a
fresh replay is right not to reproduce. The only irreproducible facts in the
store are 2,346 `gleif-isin` facts — 0.016%.

### `edgar` reproduced exactly despite being flagged unconfigured

`edgar-bulk` has no recorded CIK filter, so replay ran it with the `None`
default and produced 7,452,279 facts across 7,331 subjects. With companyfacts
and submissions that is 7,995,758 — equal to live on both count and hash.
The original ingest also ran unfiltered.

The flag is still right. `unconfigured` claims *we never recorded this*, which
is a statement about what is known, not a prediction that the output is wrong.
The comparison is what establishes the outcome matched, and the two claims are
worth keeping separate: if the universe config had named a CIK subset, the same
flag would have preceded a real divergence.

## What is still not proved

Nothing outstanding on reconstruction. ADR-0009 worried that derivation steps —
the entity graph, the security master — wrote facts `rebuild` would miss.
Measured: all 14,525,794 live facts carry adapter provenance and none has
another origin; those modules compute at read time and store nothing.

What remains is a *practice* rather than a proof: nothing runs this comparison
on a schedule, so it is a measurement taken once rather than a gate. The store
is large enough that a full replay is a six-minute job, which is too slow for
`make gate` and well within reach of the nightly deep run.
