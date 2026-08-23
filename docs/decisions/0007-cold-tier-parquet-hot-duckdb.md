# 0007 - Facts live in two tiers: a DuckDB hot table and sorted Parquet cold partitions

2026-08-23 | Status: accepted

## Context

The store was one DuckDB table. At 12,754,624 facts the database file was 1205 MB and
growing at roughly 65 MB/day, which projects to ~24 GB/year and ~50 GB for the full Phase 1
universe. The stack was specified as "DuckDB + Parquet" (spec §2.2) and only the DuckDB half
was in use.

Four query shapes — the ones `store/duck.py` actually issues — were benchmarked against
Parquet exports of the same rows, rather than against synthetic queries:

| store                       |    size | `read()` | `subject_facts` | prefix | rollup |
| --------------------------- | ------: | -------: | --------------: | -----: | -----: |
| DuckDB native               | 1205 MB |   1.8 ms |        177.7 ms | 2.8 ms | 51.4 ms |
| Parquet zstd, unsorted      |   95 MB |   6.4 ms |        188.1 ms | 3.3 ms | 58.0 ms |
| Parquet zstd, **sorted**    |   60 MB |   4.3 ms |    **160.6 ms** | 2.7 ms | 52.0 ms |
| cold Parquet + hot table    |      -- |   5.1 ms |        161.6 ms | 3.2 ms | 58.7 ms |

`PRAGMA database_size` reported 4344 of 4601 blocks used, so only 5.6% of the 1205 MB was
free list: the honest ratio is **19x against live data**, not the 20x the raw sizes suggest.

Sorting, not the codec, is what earns the size — 95.2 MB to 60.1 MB — because a sorted
`subject` column run-length encodes. It also recovers the read performance unsorted Parquet
loses, since row-group statistics on a sorted column let a subject lookup skip nearly every
row group.

**The decisive measurement is not in that table.** Parquet files are immutable. Appending one
row costs a full file rewrite: **6.7s**, against **25ms** for a 25,000-fact insert into
DuckDB. That is a 270x gap, and it grows with the store while the DuckDB append does not.

## Decision

Facts live in two tiers, unioned behind a view:

- **Hot** — the existing `facts` table. Every write goes here, unchanged.
- **Cold** — one sorted, zstd-compressed Parquet file per subject namespace, under
  `data/cold/`. Written only by `treble compact`, on a knowledge-time cutoff.

`all_facts` is a **temporary** view over both, rebuilt at every connect. A persistent view
would bake absolute paths into the database file and break the moment the data directory
moved onto another disk — which `TREBLE_DATA_DIR` exists to allow and the storage
measurements recommend.

Partitioning is by namespace because that is the axis along which the store changes: `cik` is
63% of rows and moves when EDGAR publishes, `lei` is 10% and moves when GLEIF does. A GLEIF
refresh rewrites 10% of the store rather than all of it.

Compaction is **not** on the `Store` protocol. It moves bytes between tiers without changing
which facts are visible, so it is maintenance — and putting it on the protocol would put a
deletion on an interface whose entire purpose (I2) is not having one.

### Crash safety

The step order is the whole safety argument, and it rests on one property: **a row present in
both tiers is invisible**, because every read resolves latest-knowledge-wins with
`row_number() ... WHERE rn = 1` over a partition the duplicate shares. Duplication is a
storage cost, never a wrong answer.

1. write the new Parquet under a temporary name;
2. verify it — row count *and* an order-independent hash of every column of every row —
   against the sources it was built from;
3. rename it into place (atomic);
4. only then delete the moved rows from the hot table.

Interruption at any point leaves rows in both tiers or in the hot tier alone. Never neither.
Deleting before renaming would put the only copy of the data in a file the view does not
read.

The union in step 1 is `UNION`, not `UNION ALL`, so a run interrupted between rename and
delete self-heals on retry instead of doubling the partition.

## Consequences

- The hot tier's point lookup is the only regression: 1.8 ms to 5.1 ms. Both imperceptible.
- `fact_count` counts *stored* rows across both tiers, so an interrupted compaction can make
  it read high until the next run collapses the duplicates. Every other read resolves them.
- `CHECKPOINT` does not return space to the filesystem. After the live compaction the
  database held **6 used blocks out of 4646** — 1.5 MB of data in an 859 MB file. `reclaim`
  rebuilds the file, verified by row count on every table, and is run by default from
  `treble compact`. Without it the command would truthfully report a gigabyte moved and free
  nothing a user could see.

### The defect this exposed

Compacting the live store returned a visible fact set with an **identical row count**
(8,941,289) and a **different hash**. Nothing was lost — every namespace's compaction had
verified its own rows — but `ORDER BY knowledge_from DESC` is not a total order, and **6,766
partitions hold two or more rows with the same subject, field, effective period and knowledge
time but different values**. For those, `rn = 1` was returning whichever row storage handed
back first, so re-sorting the rows on disk changed the store's answers.

This predates the cold tier. Compaction only made it observable.

`schema.TIE_BREAK` now makes the ordering total, and the three visibility windows share it
instead of being three copies of similar SQL. Verified on the live data: the visible set over
the 14,843 rows in affected partitions hashes identically under natural, shuffled and
reversed input order.

**These are not contradictions in the sources.** Broken down by field, almost all are
genuinely multi-valued facts stored under a key that assumes one value:

| field                                   | partitions |
| --------------------------------------- | ---------: |
| `gleif:rr:IS_ULTIMATELY_CONSOLIDATED_BY` |      1,794 |
| `gleif:rr:IS_ULTIMATELY_CONSOLIDATED_BY:status` | 1,402 |
| `gleif:rr:IS_DIRECTLY_CONSOLIDATED_BY`   |      1,048 |
| `gleif:rr:IS_DIRECTLY_CONSOLIDATED_BY:status` |   902 |
| `gleif:rr:IS_FUND-MANAGED_BY` (+ status) |        853 |
| `edgar:filing:form`                      |        367 |
| `swap:*` aggregates                      |        274 |
| `otc:*` derivative positions             |         49 |

GLEIF's RR-CDF lets an entity hold several relationship records at once; a filer can submit
an 8-K and a Form 4 on the same day; the `otc:` subject key
(`otc:<counterparty>:<type>:<status>`) does not distinguish individual positions, so 19
Morgan Stanley forwards share one TUID.

Every value is stored and none is lost. What the key cannot do is say which one is *the*
value, because for these fields there is no such thing. A total order makes the choice
reproducible; the fix is a discriminator in the subject or the field, which is per-field work
and is **not** attempted here. `DuckStore.ambiguous_partitions` surfaces them and
`treble compact` prints a sample so they stay visible.
