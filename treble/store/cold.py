"""Cold tier: compacted facts in sorted Parquet, unioned back at read time.

Measured on the live store (12,754,624 facts) before being built:

===========================  =========  ========  =========  ==========
store                            size    read()   subj_facts   rollup
===========================  =========  ========  =========  ==========
DuckDB native                 1205 MB    1.8 ms    177.7 ms    51.4 ms
Parquet zstd, unsorted          95 MB    6.4 ms    188.1 ms    58.0 ms
Parquet zstd, sorted            60 MB    4.3 ms    160.6 ms    52.0 ms
cold Parquet + hot table        --       5.1 ms    161.6 ms    58.7 ms
===========================  =========  ========  =========  ==========

Only 5.6% of the DuckDB file was free-list, so the ratio is 19x against
live data rather than the 20x the raw sizes suggest.

**Parquet is not a replacement for the store, and the size column is not
why.** Appending one row to a Parquet file costs a full rewrite — 6.7s
against 25ms for a 25,000-fact insert into DuckDB, a 270x gap that grows
with the store while the DuckDB append does not. So writes stay in the
table and only *settled* history moves out, on a schedule.

Partitioned by namespace because that is the axis along which the store
actually changes: `cik` is 63% of all rows and moves when EDGAR publishes,
`lei` is 10% and moves when GLEIF does. One file per namespace means a
GLEIF refresh rewrites 10% of the store rather than all of it. Sorting
within each file is what earns the size — it takes zstd from 95 MB to
60 MB, because a sorted `subject` column run-length encodes — and it is
also what keeps reads fast, since row-group statistics on a sorted column
let a subject lookup skip almost every row group.

Crash safety
------------

The ordering below is the entire safety argument, and it rests on one
property: **a row present in both tiers is invisible.** Every read
resolves latest-knowledge-wins with `row_number() ... WHERE rn = 1` over
a partition that two identical rows share, so the second copy is
discarded before it reaches a caller. Duplication is therefore a storage
cost, never a wrong answer.

That makes this order safe at every interruption point:

1. write the new Parquet to a temporary name — a crash leaves a partial
   temp file that nothing reads, and both tiers intact;
2. verify the temp file against the sources it was built from — see
   :func:`_fingerprint`;
3. rename it into place (atomic on POSIX) — a crash here leaves the rows
   in *both* tiers, which reads cannot distinguish from success;
4. delete the moved rows from the hot table — a crash mid-delete leaves
   some rows in both tiers, same harmless state.

Never the other order. Deleting before renaming would put the only copy
of the data in a file the view does not read, which is data loss wearing
the costume of a fast path.

The union in step 1 is `UNION` rather than `UNION ALL` for the same
reason step 3 is survivable: a compaction interrupted between rename and
delete leaves rows in both tiers, and the *next* compaction would union
them with themselves. Set semantics make the retry idempotent, so an
interrupted run self-heals rather than doubling the file each time. It
collapses genuinely duplicated rows too, which loses nothing a reader
could observe — only :meth:`DuckStore.fact_count`, which counts stored
rows and says so.

Nothing here is on the `Store` protocol. Compaction moves bytes between
tiers without changing which facts are visible, so it is maintenance
rather than a store operation — and putting it on the protocol would put
a `delete` on an interface whose whole point (I2) is not having one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb

from treble.store.schema import FACT_PROJECTION

#: Sits beside `treble.db` in the data directory.
COLD_DIRNAME = "cold"

#: zstd beat gzip on both size (95.2 vs 96.3 MB) and export time (1.5s vs
#: 4.7s), and snappy was 40% larger for 0.4s less. Measured, not assumed.
COMPRESSION = "zstd"

#: Namespaces are the part of a TUID before the colon and become file
#: names, so they are checked rather than trusted. A subject whose
#: namespace does not match this stays in the hot tier: refusing to
#: compact it costs space, whereas interpolating it into a path costs
#: rather more.
_NAMESPACE = re.compile(r"\A[a-z0-9_]+\Z")

_TEMP_SUFFIX = ".compacting"


class CompactionError(Exception):
    """A compaction could not be verified, so nothing was deleted."""


@dataclass(frozen=True)
class NamespaceResult:
    """What one namespace's compaction moved."""

    namespace: str
    rows_moved: int
    cold_rows: int
    cold_bytes: int

    @property
    def bytes_per_row(self) -> float:
        return self.cold_bytes / self.cold_rows if self.cold_rows else 0.0


@dataclass(frozen=True)
class CompactionReport:
    """The result of a compaction run across every namespace touched."""

    results: tuple[NamespaceResult, ...]
    skipped: tuple[str, ...]

    @property
    def rows_moved(self) -> int:
        return sum(r.rows_moved for r in self.results)

    @property
    def cold_bytes(self) -> int:
        return sum(r.cold_bytes for r in self.results)

    @property
    def moved_anything(self) -> bool:
        return self.rows_moved > 0


def cold_dir_for(db_path: Path) -> Path:
    return db_path.parent / COLD_DIRNAME


def cold_files(cold_dir: Path) -> tuple[Path, ...]:
    """Every complete cold partition, sorted by name so the view SQL is stable.

    A partial file from an interrupted compaction must never be read — it
    is truncated by definition, and a view over one would present a
    half-written partition as missing data. What keeps it out is the
    *name*: `_TEMP_SUFFIX` goes on the end, so `cik.parquet.compacting`
    does not match this glob.

    That is the whole mechanism, and it is stated here because it was
    briefly written twice — once in the naming and once as an `endswith`
    filter on this line. The filter could never fire, since anything it
    would have rejected had already failed the glob, so it was a guard
    testing a condition that could not occur. `test_the_temp_suffix_is_a
    _name_the_glob_cannot_match` is what actually holds the property now,
    and it fails if the suffix ever changes shape.
    """
    if not cold_dir.is_dir():
        return ()
    return tuple(sorted(cold_dir.glob("*.parquet")))


def _partition_path(cold_dir: Path, namespace: str) -> Path:
    return cold_dir / f"{namespace}.parquet"


def union_sql(cold_dir: Path) -> str:
    """The physical fact set: the hot table plus every cold partition.

    `UNION ALL` here, unlike in compaction — this is the read path, where
    a duplicate across tiers is already discarded by the visibility
    window, and paying for set semantics on every query to remove rows
    that are about to be filtered anyway would be a real cost for no
    change in answers.

    Columns are projected explicitly on both sides. A cold file written
    before a schema change is then a loud error at view creation rather
    than a silent positional mismatch.
    """
    hot = f"SELECT {FACT_PROJECTION} FROM facts"  # noqa: S608
    files = cold_files(cold_dir)
    if not files:
        return hot
    paths = ", ".join(f"'{_escape(str(p))}'" for p in files)
    return f"{hot} UNION ALL SELECT {FACT_PROJECTION} FROM read_parquet([{paths}])"  # noqa: S608


def _escape(literal: str) -> str:
    return literal.replace("'", "''")


def _fingerprint(
    conn: duckdb.DuckDBPyConnection, source: str, params: list[object]
) -> tuple[int, int]:
    """Row count and an order-independent hash of every column of every row.

    `sum` rather than `bit_xor`: XOR cancels in pairs, so a file holding
    each row exactly twice would fingerprint identically to one holding
    each row once — which is precisely the corruption this exists to
    catch. Summing does not cancel, and casting to HUGEINT means the sum
    of 8 million 64-bit hashes cannot wrap around into a collision.
    """
    row = conn.execute(
        f"SELECT count(*), coalesce(sum(hash({FACT_PROJECTION})::HUGEINT), 0) FROM ({source})",  # noqa: S608
        params,
    ).fetchone()
    if row is None:  # pragma: no cover - aggregate always returns a row
        raise CompactionError(f"fingerprint query returned nothing: {source}")
    return int(row[0]), int(row[1])


def namespaces_to_compact(
    conn: duckdb.DuckDBPyConnection, *, before: datetime
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Namespaces with settled hot rows, and those refused as unsafe names."""
    rows = conn.execute(
        "SELECT DISTINCT split_part(subject, ':', 1) FROM facts WHERE knowledge_from < ?",
        [before],
    ).fetchall()
    names = sorted(str(r[0]) for r in rows)
    safe = tuple(n for n in names if _NAMESPACE.match(n))
    return safe, tuple(n for n in names if not _NAMESPACE.match(n))


def compact(
    conn: duckdb.DuckDBPyConnection,
    cold_dir: Path,
    *,
    before: datetime,
    namespaces: tuple[str, ...] | None = None,
) -> CompactionReport:
    """Move facts known before ``before`` out of the hot table into Parquet.

    ``before`` is a knowledge-time cutoff, not an effective-time one: it
    selects rows the store has finished learning about. Facts arriving
    today stay hot, so an ingest in progress is never competing with a
    rewrite of the partition it is writing into.
    """
    if before.tzinfo is None:
        raise ValueError("before must be timezone-aware")
    cold_dir.mkdir(parents=True, exist_ok=True)
    candidates, refused = namespaces_to_compact(conn, before=before)
    if namespaces is not None:
        wanted = set(namespaces)
        unknown = wanted - set(candidates) - set(refused)
        if unknown:
            raise CompactionError(f"no settled facts in namespace(s): {sorted(unknown)}")
        candidates = tuple(n for n in candidates if n in wanted)
        refused = tuple(n for n in refused if n in wanted)

    results = [_compact_one(conn, cold_dir, namespace=ns, before=before) for ns in candidates]
    return CompactionReport(results=tuple(results), skipped=refused)


def _compact_one(
    conn: duckdb.DuckDBPyConnection,
    cold_dir: Path,
    *,
    namespace: str,
    before: datetime,
) -> NamespaceResult:
    target = _partition_path(cold_dir, namespace)
    temp = target.with_name(target.name + _TEMP_SUFFIX)
    prefix = f"{namespace}:"

    hot = (
        f"SELECT {FACT_PROJECTION} FROM facts "  # noqa: S608
        "WHERE starts_with(subject, ?) AND knowledge_from < ?"
    )
    hot_params: list[object] = [prefix, before]
    if target.exists():
        source = (
            f"SELECT {FACT_PROJECTION} FROM read_parquet('{_escape(str(target))}') "  # noqa: S608
            f"UNION {hot}"
        )
    else:
        source = hot
    rows_moved, _ = _fingerprint(conn, hot, list(hot_params))
    expected_rows, expected_hash = _fingerprint(conn, source, list(hot_params))

    # Sorted by the columns reads filter and group on. This is where the
    # 95 MB -> 60 MB comes from, and where `subject_facts` gets back the
    # 17ms that unsorted Parquet loses.
    conn.execute(
        f"COPY ({source} ORDER BY subject, field, effective_from, knowledge_from) "
        f"TO '{_escape(str(temp))}' (FORMAT PARQUET, COMPRESSION {COMPRESSION})",
        hot_params,
    )

    # Verify before anything becomes irreversible. Reading the file back
    # catches a truncated write, a codec fault, and a type that did not
    # survive the round trip — none of which the COPY reports.
    written_rows, written_hash = _fingerprint(
        conn,
        f"SELECT {FACT_PROJECTION} FROM read_parquet('{_escape(str(temp))}')",  # noqa: S608
        [],
    )
    if (written_rows, written_hash) != (expected_rows, expected_hash):
        temp.unlink(missing_ok=True)
        raise CompactionError(
            f"{namespace}: wrote {written_rows} rows (hash {written_hash}), "
            f"expected {expected_rows} (hash {expected_hash}); hot tier untouched"
        )

    temp.replace(target)
    conn.execute(
        "DELETE FROM facts WHERE starts_with(subject, ?) AND knowledge_from < ?",
        [prefix, before],
    )
    return NamespaceResult(
        namespace=namespace,
        rows_moved=rows_moved,
        cold_rows=written_rows,
        cold_bytes=target.stat().st_size,
    )


def _count(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    """Row count for one table, with the empty result treated as an error.

    `fetchone()` is typed as optional and an aggregate always returns a
    row, but this feeds the check that decides whether a rebuilt database
    replaces the working one — so a `None` silently coerced to zero would
    make two empty-looking tables compare equal and wave the replacement
    through.
    """
    row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
    if row is None:  # pragma: no cover - an aggregate always returns a row
        raise CompactionError(f"count query over {table!r} returned no row")
    return int(row[0])


def reclaim(conn: duckdb.DuckDBPyConnection, db_path: Path) -> tuple[int, int]:
    """Rebuild the database file so the compacted space returns to the disk.

    `CHECKPOINT` does not do this, and the difference is the entire point
    of compacting. After moving 12.7 million facts out of the live store,
    `PRAGMA database_size` reported **6 used blocks out of 4646** — 1.5 MB
    of data in an 859 MB file. DuckDB reuses freed blocks for future
    writes but never returns them to the filesystem, so a compaction that
    stopped at `CHECKPOINT` would truthfully report a gigabyte moved and
    free nothing a user could see.

    The rebuild is a copy into a fresh file, verified by row count on
    every table, and then an atomic rename. The original is untouched
    until the replacement exists and has been checked, so an interruption
    leaves the working store in place and a stray temp file behind.

    Returns (bytes before, bytes after). The caller's connection is closed
    and must be reopened — the file underneath it has been replaced.
    """
    before = db_path.stat().st_size
    temp = db_path.with_name(db_path.name + ".rebuilding")
    temp.unlink(missing_ok=True)

    counts = {table: _count(conn, table) for table in ("facts", "provenance")}
    # Asked rather than derived from the filename: DuckDB's name for the
    # attached database is not always the path stem, and getting it wrong
    # would copy the wrong database into the replacement.
    current = conn.execute("SELECT current_database()").fetchone()
    if current is None:  # pragma: no cover - always returns a row
        raise CompactionError("could not determine the current database name")
    conn.execute(f"ATTACH '{_escape(str(temp))}' AS rebuilt")
    conn.execute(f'COPY FROM DATABASE "{current[0]}" TO rebuilt')
    conn.execute("DETACH rebuilt")
    conn.close()

    check = duckdb.connect(str(temp), read_only=True)
    try:
        rebuilt = {table: _count(check, table) for table in counts}
    finally:
        check.close()
    if rebuilt != counts:
        temp.unlink(missing_ok=True)
        raise CompactionError(
            f"rebuilt database has {rebuilt} rows, original had {counts}; original kept"
        )

    temp.replace(db_path)
    return before, db_path.stat().st_size


__all__ = [
    "COLD_DIRNAME",
    "COMPRESSION",
    "CompactionError",
    "CompactionReport",
    "NamespaceResult",
    "cold_dir_for",
    "cold_files",
    "compact",
    "namespaces_to_compact",
    "reclaim",
    "union_sql",
]
