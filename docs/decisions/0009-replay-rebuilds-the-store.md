# 0009 — Replay rebuilds the store, and what it proved

**Status:** accepted
**Date:** 2026-08-25
**Closes the open item in:** [0008 — the storage budget is a gate](0008-storage-budget-as-a-gate.md)

## Context

ADR-0008 ended with an admission: `data/payloads` is kept because the database
is derived and could be rebuilt from it, but **no command did the rebuilding**.
`SourceAdapter.replay` had re-parsed one source's log since Phase 1, and three
of nineteen adapters had a test for it. Nothing replayed the whole log, and
nothing had ever compared the result against the store it claimed to reproduce.

That is failure mode E — an explanation recorded as fact, never tested — and it
had a cost. Sessions made 336 MB copies of the database before risky work,
because nobody could demonstrate that the 604 MB of payloads was sufficient.

## Decision

`treble replay --into <new.db>` re-derives a store from payloads and the ingest
log with no network. It writes a **new** file and refuses an existing one: the
point of a replay is comparison, and writing over the original destroys the
only thing worth comparing against.

### Constructing an adapter that parses but cannot fetch

Adapters take fetch configuration — `series`, `ciks`, `symbols`, an API key.
`parse` should need none of it. `replay.parse_only` therefore does not call
`__init__` at all: it allocates the instance and sets the three attributes the
base class uses. **Any `parse` that reaches for fetch configuration raises
`AttributeError` naming the attribute**, rather than being handed an invented
default whose effect would surface later as a divergence indistinguishable
from a parser change.

That mechanism is the reason this ADR has findings in it.

## What the replay measured

Against the live log — **488 payloads, 18 sources, 6,087,257 facts re-derived
in 235s with no network**:

### Identical (count and order-independent hash over all twelve fact columns)

`coinbase`, `coinbase-products`, `ecb-hicp`, `fred`, `frenchdata`, `gleif`,
`openfigi`, `treasury-curve`, `twelvedata` — **nine sources, 3,619,507 facts,
bit-identical**. This is the claim ADR-0008 could not make.

### Identical in content; provenance ids differ

`ecb` → `ecb-fx` and `treasury-fiscaldata` → `treasury-auctions` are renames of
`source_system`. Provenance ids are derived from their fields, so a rename
changes the id and therefore every `provenance_id` on every fact. Excluding
that one column, live and replayed are identical: 63,609 and 7,520 facts,
exact on both count and hash.

### Subsets of live, correctly

`dtcc-sdr`, `sec-nport`, `edgar-submissions` — every replayed fact's content is
already in the live store; the live store holds more. That is the right
relationship. Replay produces what *today's* parsers yield; the live store also
retains what older parser versions produced, because nothing is deleted (I2).
`sec-nport` is the currency fix, `dtcc-sdr` the report-window change.

### One superset, which invented nothing

`gleif-rr` replayed 1,990,155 facts, of which 1,326,766 are not in the live
store. The three `gleif-rr` log entries are overlapping fetches of the same
file, 09:50:20, 09:52:18 and 09:56:10 on 2026-08-09, so each relationship
appears in all three payloads and replay records one observation per payload —
660,256 keys carry more than one knowledge time. The live store kept the last.

**Zero** of those extra facts have a `(subject, field, value, effective period)`
absent from the live store. Replay invented nothing; it recorded the same
values at the earlier knowledge times they were genuinely observed at, which is
what I2 asks for. No query answer changes: the visibility window returns the
latest, which both stores hold.

### Three adapters could not replay at all (fixed — ADR-0010)

Found by `parse_only` raising, and pinned in `tests/ingest/test_replay.py`:

| adapter | wants | effect on a replay |
|---|---|---|
| `edgar-companyfacts` | `_accepted` | knowledge times come from a *different source* (`edgar-submissions`); without it, parse falls back to end-of-day filed date — coarser I2 resolution |
| `edgar-bulk` | `_ciks` | a filter; `None` means no filter, so replay would produce a **superset** of the original ingest |
| `gleif-isin` | `_isins` | a filter with no escape, so replay produces **zero facts** |

All three are the same defect: **the store's content depends on fetch-time
configuration the ingest log does not record.** The log holds `source`,
`payload_hash`, `source_uri`, `fetched_at`, `parser_version` — and none of the
arguments that changed what `parse` did with the bytes.

**Fixed in ADR-0010.** Two of the three record their configuration in a new
`parse_config` column; the third derives it and needed no column at all.

## Consequences

- The payloads are now demonstrably worth keeping, and the database
  demonstrably is not the thing to back up. `storage.measure` already refuses
  to call `payloads/` reclaimable; this is the evidence for that refusal.
- `treble replay` exits non-zero when it could not re-derive everything. A
  replay that quietly skipped a source would report success on a smaller store.
- `NEEDS_RECORDED_CONFIG` in the tests is a backlog that should shrink. An
  adapter added with a fetch-dependent `parse` lands there, and the test that
  pins the set is where it gets noticed.

## What is still not proved

Replay reconstructs **adapter-derived facts**. The live store also holds facts
produced by derivation steps that run over those facts — the entity graph, the
security master — and `rebuild` does not run them. A full reconstruction is
replay followed by those steps, and the second half has not been measured. The
live store's 14.5M facts against replay's 6.1M is mostly the three unreplayable
sources plus superseded parser output, but "mostly" is doing real work in that
sentence and it has not been decomposed.
