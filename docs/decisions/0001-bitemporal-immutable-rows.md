# 0001 - Bitemporal rows are immutable; knowledge_to is derived, not stored

2026-07-25 | Status: accepted

## Context

Invariant I2 (CLAUDE.md §1) requires bitemporality: every fact carries an effective period and
a knowledge period, and "restatements create new rows; they never update old ones." The
conventional bitemporal implementation *closes* the superseded row by setting its
`knowledge_to` — which is itself an UPDATE, contradicting the append-only requirement and
re-opening the door to the failure mode I2 exists to prevent (history silently rewritten).

## Decision

Fact rows are fully immutable and store only `knowledge_from` (plus
`effective_from`/`effective_to`). `knowledge_to` is not a column; it is derived at query time
as the `knowledge_from` of the superseding row for the same (entity, field, effective period).
The `Store`/`HistoryStore` protocols expose no update or delete methods at all, so append-only
is a property of the interface, not a discipline. Reads take a required timezone-aware
`as_of` parameter and resolve latest-knowledge-wins with `knowledge_from <= as_of`.

For fundamentals, `knowledge_from` is the EDGAR `accepted` timestamp (CLAUDE.md §6), never the
period end or cover-page date.

## Consequences

- Easy: append-only is mechanically guaranteed; deterministic replay (I5) over the store is
  trivially consistent; Parquet/DuckDB write paths are insert-only.
- Hard: point-in-time reads need a window function (latest `knowledge_from` per key ≤ `as_of`)
  rather than a simple range predicate; the query layer owns that complexity once, and an index
  on (key, knowledge_from) keeps it cheap at our scale.
- Forecloses: physical deletion/correction of bad rows. Corrections are new rows superseding
  the old, with provenance explaining why — which is exactly the auditable behaviour the spec
  (§5.4, §8.1.5) demands.
