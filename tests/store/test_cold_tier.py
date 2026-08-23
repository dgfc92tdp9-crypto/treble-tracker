"""The cold tier must be invisible to every reader.

Compaction is the only operation in the system that removes rows from the
hot table, so the burden of proof here is higher than usual: it is not
enough that reads *happen* to agree after a successful run. Each claim
`cold.py` makes in its docstring is exercised against the state it
describes, including the states that only occur when a compaction is
interrupted — because "a crash there is harmless" is exactly the kind of
explanation that gets recorded as fact and never tested.
"""

from __future__ import annotations

# ruff: noqa: S608 - test SQL is built from pytest tmp_path, not caller input
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

import duckdb
import pytest

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.cold import (
    _TEMP_SUFFIX,
    CompactionError,
    cold_files,
    compact,
    reclaim,
    union_sql,
)
from treble.store.duck import DuckStore
from treble.store.schema import SCHEMA

OLD = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
NEWER = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
CUTOFF = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
NOW = datetime(2026, 8, 20, 0, 0, tzinfo=UTC)


def _provenance(tag: str) -> Provenance:
    return Provenance(
        source_system="edgar",
        source_uri=f"https://example.invalid/{tag}",
        retrieved_at=OLD,
        method=ExtractionMethod.BULK_FILE,
        extractor_version="1",
        payload_hash=tag[:1] * 64,
    )


def _fact(subject: str, field: str, value: object, known: datetime, pid: str, **kw: object) -> Fact:
    return Fact(
        subject=TUID(subject),
        field=field,
        value=value,  # type: ignore[arg-type]
        effective_from=kw.get("effective_from", date(2025, 12, 31)),  # type: ignore[arg-type]
        effective_to=kw.get("effective_to"),  # type: ignore[arg-type]
        knowledge_from=known,
        provenance_id=pid,  # type: ignore[arg-type]
    )


def _populated(tmp_path: Path) -> DuckStore:
    """A store spanning the cutoff, with a restatement across it.

    The restatement matters: `cik:1` / `Revenue` is filed old and revised
    new, so after compaction the two versions of one fact live in
    *different tiers*. Latest-knowledge-wins has to resolve across the
    boundary or point-in-time reads silently answer from one tier.
    """
    store = DuckStore(tmp_path / "t.db")
    old, new = _provenance("aold"), _provenance("bnew")
    store.write_provenance([old, new])
    store.write_facts(
        [
            _fact("cik:1", "Revenue", 100.0, OLD, old.id),
            _fact("cik:1", "Revenue", 111.0, NEWER, new.id),  # restatement
            _fact("cik:1", "Assets", 900, OLD, old.id),
            _fact("cik:2", "Revenue", 200.0, OLD, old.id),
            _fact("lei:X", "legalName", "Acme", OLD, old.id),
            _fact("lei:X", "status", True, NEWER, new.id),
            _fact("isin:Z", "maturity", date(2030, 6, 1), OLD, old.id),
            _fact("isin:Z", "coupon", None, OLD, old.id),
        ]
    )
    return store


def _snapshot(store: DuckStore) -> dict[str, object]:
    """Everything a caller can observe, through every read the store has."""
    return {
        "cik1_rev": store.read(TUID("cik:1"), "Revenue", as_of=NOW),
        "cik1_rev_early": store.read(TUID("cik:1"), "Revenue", as_of=CUTOFF),
        "cik1_all": store.subject_facts(TUID("cik:1"), as_of=NOW),
        "isin_all": store.subject_facts(TUID("isin:Z"), as_of=NOW),
        "cik_prefix": store.subjects_with_prefix("cik:", as_of=NOW),
        "by_value": store.subjects_with_value("legalName", "Acme", as_of=NOW),
        "has": store.has_subject(TUID("lei:X")),
        "prov": sorted(store.subject_provenance(TUID("cik:1"), as_of=NOW)),
        "history": store.history(TUID("cik:1"), "Revenue", as_of=NOW),
    }


class TestCompactionChangesNothingObservable:
    def test_every_read_agrees_before_and_after(self, tmp_path: Path) -> None:
        store = _populated(tmp_path)
        before = _snapshot(store)
        report = store.compact(before=CUTOFF)
        assert report.moved_anything
        assert _snapshot(store) == before

    def test_the_rows_really_did_move(self, tmp_path: Path) -> None:
        """Guards against a compaction that copies and never deletes —
        which would pass every other test in this file while doubling
        storage instead of reducing it."""
        store = _populated(tmp_path)
        store.compact(before=CUTOFF)
        hot = store._conn.execute("SELECT count(*) FROM facts").fetchone()
        assert hot is not None
        assert hot[0] == 2, "only the two post-cutoff facts should remain hot"
        assert len(cold_files(store.cold_dir)) == 3  # cik, lei, isin

    def test_a_restatement_still_wins_across_the_tier_boundary(self, tmp_path: Path) -> None:
        store = _populated(tmp_path)
        store.compact(before=CUTOFF)
        current = store.read(TUID("cik:1"), "Revenue", as_of=NOW)
        assert [f.value for f in current] == [111.0]

    def test_point_in_time_still_sees_the_superseded_value(self, tmp_path: Path) -> None:
        """The cold row is the *older* one here, so a read as of the
        cutoff must answer from the cold tier alone."""
        store = _populated(tmp_path)
        store.compact(before=CUTOFF)
        assert [f.value for f in store.read(TUID("cik:1"), "Revenue", as_of=CUTOFF)] == [100.0]

    @pytest.mark.parametrize(
        ("field", "expected"),
        [("Assets", 900), ("Revenue", 111.0)],
    )
    def test_value_types_survive_the_parquet_round_trip(
        self, tmp_path: Path, field: str, expected: object
    ) -> None:
        store = _populated(tmp_path)
        store.compact(before=CUTOFF)
        value = store.read(TUID("cik:1"), field, as_of=NOW)[0].value
        assert value == expected and type(value) is type(expected)

    def test_null_and_date_values_survive(self, tmp_path: Path) -> None:
        store = _populated(tmp_path)
        store.compact(before=CUTOFF)
        got = {f.field: f.value for f in store.subject_facts(TUID("isin:Z"), as_of=NOW)}
        assert got == {"maturity": date(2030, 6, 1), "coupon": None}


class TestWritesKeepWorkingAfterCompaction:
    def test_a_new_fact_lands_beside_the_cold_ones(self, tmp_path: Path) -> None:
        store = _populated(tmp_path)
        store.compact(before=CUTOFF)
        record = _provenance("cnew")
        store.write_provenance([record])
        store.write_facts([_fact("cik:1", "Revenue", 222.0, NOW, record.id)])
        assert [f.value for f in store.read(TUID("cik:1"), "Revenue", as_of=NOW)] == [222.0]
        assert [f.value for f in store.read(TUID("cik:1"), "Revenue", as_of=CUTOFF)] == [100.0]

    def test_a_second_compaction_moves_the_new_fact_too(self, tmp_path: Path) -> None:
        store = _populated(tmp_path)
        store.compact(before=CUTOFF)
        record = _provenance("cnew")
        store.write_provenance([record])
        store.write_facts([_fact("cik:3", "Revenue", 5.0, NEWER, record.id)])
        before = _snapshot(store)
        store.compact(before=NOW)
        assert _snapshot(store) == before
        hot = store._conn.execute("SELECT count(*) FROM facts").fetchone()
        assert hot is not None and hot[0] == 0


class TestTheViewSurvivesReconnection:
    def test_a_fresh_store_object_reads_the_cold_tier(self, tmp_path: Path) -> None:
        """The view is TEMP, so a new process rebuilds it. If that were
        forgotten, compacted history would vanish on restart — and only
        on restart, which is the worst possible time to find out."""
        store = _populated(tmp_path)
        expected = _snapshot(store)
        store.compact(before=CUTOFF)
        store._conn.close()
        assert _snapshot(DuckStore(tmp_path / "t.db")) == expected

    def test_the_cold_tier_moves_with_the_data_directory(self, tmp_path: Path) -> None:
        """`TREBLE_DATA_DIR` exists so the store can live on another disk,
        and the storage measurements recommend doing exactly that. A view
        with absolute paths baked into the database file would not
        survive the move."""
        (tmp_path / "here").mkdir()
        store = _populated(tmp_path / "here")
        expected = _snapshot(store)
        store.compact(before=CUTOFF)
        store._conn.close()
        (tmp_path / "here").rename(tmp_path / "there")
        assert _snapshot(DuckStore(tmp_path / "there" / "t.db")) == expected


class TestInterruption:
    """The states a crash can leave behind, constructed deliberately."""

    def test_a_row_in_both_tiers_is_invisible(self, tmp_path: Path) -> None:
        """The property the whole ordering rests on. Simulated by running
        the compaction and re-inserting the moved rows into the hot table,
        which is exactly the state a crash between rename and delete
        leaves — the same rows, in both places."""
        store = _populated(tmp_path)
        expected = _snapshot(store)
        store.compact(before=CUTOFF)
        # Put every cold row back into the hot table.
        for path in cold_files(store.cold_dir):
            store._conn.execute(f"INSERT INTO facts SELECT * FROM read_parquet('{path}')")
        duplicated = store._conn.execute("SELECT count(*) FROM all_facts").fetchone()
        assert duplicated is not None and duplicated[0] == 14  # 8 facts, 6 of them twice
        assert _snapshot(store) == expected, "duplication across tiers changed an answer"

    def test_recompacting_that_state_collapses_the_duplicates(self, tmp_path: Path) -> None:
        """`UNION` rather than `UNION ALL` in the compaction source. With
        `UNION ALL` an interrupted run would double the partition every
        time it was retried."""
        store = _populated(tmp_path)
        expected = _snapshot(store)
        store.compact(before=CUTOFF)
        for path in cold_files(store.cold_dir):
            store._conn.execute(f"INSERT INTO facts SELECT * FROM read_parquet('{path}')")
        store.compact(before=CUTOFF)
        total = store._conn.execute("SELECT count(*) FROM all_facts").fetchone()
        assert total is not None and total[0] == 8, "retry did not collapse the duplicates"
        assert _snapshot(store) == expected

    def test_a_partial_temp_file_is_not_read(self, tmp_path: Path) -> None:
        """A crash during the COPY leaves a `.compacting` file. It is
        partial by definition; a view that read it would present a
        half-written partition as missing data."""
        store = _populated(tmp_path)
        store.compact(before=CUTOFF)
        expected = _snapshot(store)
        (store.cold_dir / "cik.parquet.compacting").write_bytes(b"PAR1 garbage")
        store._refresh_view()
        assert _snapshot(store) == expected


class _Sabotaged:
    """A connection that damages every Parquet file `compact` writes.

    DuckDB's connection object refuses attribute assignment, so the
    corruption is injected by delegation rather than monkeypatching. Every
    other statement — including the fingerprint queries — passes straight
    through to the real connection, so what is being tested is the real
    verification against a real hot table.
    """

    def __init__(self, inner: duckdb.DuckDBPyConnection, damage: Callable[[Path], None]) -> None:
        self._inner = inner
        self._damage = damage

    def execute(self, sql: str, *args: object, **kw: object) -> object:
        result = self._inner.execute(sql, *args, **kw)  # type: ignore[arg-type]
        if sql.startswith("COPY "):
            self._damage(Path(sql.split("TO '")[1].split("'")[0]))
        return result

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class _BreakOnDetach:
    """Corrupts the rebuilt file after it is written but before it is checked.

    The window that matters: the copy has succeeded, so a rebuild that
    trusted `COPY FROM DATABASE` would rename this over the working store.
    """

    def __init__(self, inner: duckdb.DuckDBPyConnection, temp: Path) -> None:
        self._inner = inner
        self._temp = temp

    def execute(self, sql: str, *args: object, **kw: object) -> object:
        result = self._inner.execute(sql, *args, **kw)  # type: ignore[arg-type]
        if sql.startswith("DETACH"):
            # A *valid* database with the wrong contents, not a corrupt
            # file. Garbage bytes raise from the count query on their own,
            # so they would pass whether or not the counts are compared —
            # and the comparison is the thing being tested.
            self._temp.unlink(missing_ok=True)
            empty = duckdb.connect(str(self._temp))
            empty.execute(SCHEMA)
            empty.close()
        return result

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def _as_conn(sabotaged: _Sabotaged) -> duckdb.DuckDBPyConnection:
    return cast("duckdb.DuckDBPyConnection", sabotaged)


class TestVerificationGuardsTheDelete:
    def test_nothing_is_deleted_when_the_file_is_unreadable(self, tmp_path: Path) -> None:
        """The guard must be able to fail. Corrupting the COPY output
        before verification reads it is the only way to know the check is
        wired to the delete rather than merely present."""
        store = _populated(tmp_path)
        before = _snapshot(store)
        conn = _as_conn(_Sabotaged(store._conn, lambda p: p.write_bytes(b"not parquet")))

        with pytest.raises((CompactionError, duckdb.Error)):
            compact(conn, store.cold_dir, before=CUTOFF)

        store._refresh_view()
        assert _snapshot(store) == before, "hot tier was touched despite a failed verification"
        hot = store._conn.execute("SELECT count(*) FROM facts").fetchone()
        assert hot is not None and hot[0] == 8, "rows were deleted before verification passed"
        assert cold_files(store.cold_dir) == ()

    def test_a_valid_file_with_one_changed_value_is_caught(self, tmp_path: Path) -> None:
        """Row count alone would pass this. The replacement is a
        well-formed Parquet file with the right schema and the right
        number of rows, differing only in one number — the corruption a
        count-based check cannot see, and the reason the fingerprint
        hashes every column rather than counting rows."""
        store = _populated(tmp_path)
        before = _snapshot(store)

        def bump_one_number(path: Path) -> None:
            side = duckdb.connect(":memory:")
            side.execute(
                f"CREATE TABLE t AS SELECT * REPLACE "
                f"(coalesce(value_num, 0) + 1 AS value_num) "
                f"FROM read_parquet('{path}')"
            )
            side.execute(f"COPY t TO '{path}' (FORMAT PARQUET)")
            side.close()

        conn = _as_conn(_Sabotaged(store._conn, bump_one_number))
        with pytest.raises(CompactionError, match="expected"):
            compact(conn, store.cold_dir, before=CUTOFF)

        store._refresh_view()
        assert _snapshot(store) == before
        assert cold_files(store.cold_dir) == ()
        assert not list(store.cold_dir.glob("*.compacting")), "temp file left behind"


class TestUnsafeNamespacesAreRefused:
    def test_a_namespace_that_is_not_a_safe_file_name_stays_hot(self, tmp_path: Path) -> None:
        store = DuckStore(tmp_path / "t.db")
        record = _provenance("aold")
        store.write_provenance([record])
        store.write_facts(
            [
                _fact("../escape:1", "Revenue", 1.0, OLD, record.id),
                _fact("cik:1", "Revenue", 2.0, OLD, record.id),
            ]
        )
        report = store.compact(before=CUTOFF)
        assert report.skipped == ("../escape",)
        assert [p.name for p in cold_files(store.cold_dir)] == ["cik.parquet"]
        assert not list(store.cold_dir.parent.glob("*escape*"))
        assert store.read(TUID("../escape:1"), "Revenue", as_of=NOW)[0].value == 1.0


class TestReportingAndEdges:
    def test_nothing_settled_is_not_an_error(self, tmp_path: Path) -> None:
        store = _populated(tmp_path)
        report = store.compact(before=datetime(2020, 1, 1, tzinfo=UTC))
        assert not report.moved_anything
        assert cold_files(store.cold_dir) == ()

    def test_an_empty_store_compacts_to_nothing(self, tmp_path: Path) -> None:
        assert not DuckStore(tmp_path / "e.db").compact(before=NOW).moved_anything

    def test_a_naive_cutoff_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            _populated(tmp_path).compact(before=datetime(2026, 2, 1))  # noqa: DTZ001

    def test_an_unknown_namespace_is_named(self, tmp_path: Path) -> None:
        with pytest.raises(CompactionError, match="nosuch"):
            _populated(tmp_path).compact(before=CUTOFF, namespaces=("nosuch",))

    def test_one_namespace_can_be_compacted_alone(self, tmp_path: Path) -> None:
        store = _populated(tmp_path)
        expected = _snapshot(store)
        report = store.compact(before=CUTOFF, namespaces=("lei",))
        assert [r.namespace for r in report.results] == ["lei"]
        assert [p.name for p in cold_files(store.cold_dir)] == ["lei.parquet"]
        assert _snapshot(store) == expected

    def test_the_report_counts_what_moved(self, tmp_path: Path) -> None:
        report = _populated(tmp_path).compact(before=CUTOFF)
        by_ns = {r.namespace: r for r in report.results}
        assert by_ns["cik"].rows_moved == 3
        assert by_ns["lei"].rows_moved == 1
        assert by_ns["isin"].rows_moved == 2
        assert report.rows_moved == 6
        assert all(r.cold_bytes > 0 and r.bytes_per_row > 0 for r in report.results)

    def test_union_sql_names_the_table_alone_when_there_is_no_cold_tier(
        self, tmp_path: Path
    ) -> None:
        assert union_sql(tmp_path / "absent") == union_sql(tmp_path)
        assert "read_parquet" not in union_sql(tmp_path)


class TestFactCountSpansBothTiers:
    def test_the_count_does_not_drop_when_rows_move(self, tmp_path: Path) -> None:
        """A count that read only the hot table would fall from 8 to 2 and
        make a compacted store look unpopulated — which is precisely the
        condition `fact_count` exists to detect."""
        store = _populated(tmp_path)
        assert store.fact_count() == 8
        store.compact(before=CUTOFF)
        assert store.fact_count() == 8


class TestTheColdTierActuallyShrinks:
    def test_a_compacted_partition_is_smaller_than_the_rows_it_replaced(
        self, tmp_path: Path
    ) -> None:
        """Small fixtures cannot show the 19x measured on the live store —
        Parquet's footer dominates at this scale. What is checkable here
        is that the file is written, sorted, and readable; the ratio is
        measured against real data in the benchmark, not asserted here."""
        store = _populated(tmp_path)
        store.compact(before=CUTOFF)
        path = store.cold_dir / "cik.parquet"
        rows = (
            duckdb.connect(":memory:")
            .execute(f"SELECT subject, field FROM read_parquet('{path}')")
            .fetchall()
        )
        assert rows == sorted(rows), "partition is not sorted, which is where the size comes from"


class TestAmbiguousPartitionsResolveDeterministically:
    """Found by compacting the live store, not by writing a test.

    The visible fact set came back with an identical row count —
    8,941,289 both times — and a different hash. Nothing had been lost;
    6,766 partitions hold two or more rows with the same subject, field,
    effective period *and* knowledge time but different values, and
    `ORDER BY knowledge_from DESC` cannot rank those. `rn = 1` was
    returning whichever row storage handed back first, so re-sorting the
    rows on disk changed the store's answers.
    """

    @staticmethod
    def _conflicting(directory: Path, *, reverse: bool) -> DuckStore:
        directory.mkdir(parents=True)
        store = DuckStore(directory / "t.db")
        record = _provenance("aold")
        store.write_provenance([record])
        facts = [
            _fact("cik:1", "Revenue", 100.0, OLD, record.id),
            _fact("cik:1", "Revenue", 200.0, OLD, record.id),
        ]
        store.write_facts(list(reversed(facts)) if reverse else facts)
        return store

    def test_physical_order_does_not_decide_the_answer(self, tmp_path: Path) -> None:
        forward = self._conflicting(tmp_path / "a", reverse=False)
        backward = self._conflicting(tmp_path / "b", reverse=True)
        assert forward.read(TUID("cik:1"), "Revenue", as_of=NOW) == backward.read(
            TUID("cik:1"), "Revenue", as_of=NOW
        )

    def test_compaction_does_not_change_the_chosen_value(self, tmp_path: Path) -> None:
        """The live failure in miniature: compaction sorts the rows, and
        before the tie-break was total that re-sort silently changed which
        of two conflicting values the store reported as current."""
        store = self._conflicting(tmp_path / "a", reverse=False)
        before = store.read(TUID("cik:1"), "Revenue", as_of=NOW)
        store.compact(before=CUTOFF)
        assert store.read(TUID("cik:1"), "Revenue", as_of=NOW) == before

    def test_all_three_windows_agree(self, tmp_path: Path) -> None:
        """`read`, `subject_facts` and `subject_provenance` each resolve
        visibility with their own window. They were separate copies of the
        same SQL, so they could — and briefly did — differ in ordering.

        `reverse=True` matters: with the rows stored in tie-break order
        the windows agree whether or not the ordering is total, so this
        test passed against a `subject_facts` that was still ranking by
        `knowledge_from` alone. Storing them the other way round makes the
        tie-break's choice and the physical first row different rows.
        """
        store = self._conflicting(tmp_path / "a", reverse=True)
        from_read = store.read(TUID("cik:1"), "Revenue", as_of=NOW)
        from_all = store.subject_facts(TUID("cik:1"), as_of=NOW)
        assert from_read == from_all
        assert store.subject_provenance(TUID("cik:1"), as_of=NOW) == [
            f.provenance_id for f in from_read
        ]

    def test_the_conflict_is_reported_rather_than_hidden(self, tmp_path: Path) -> None:
        store = self._conflicting(tmp_path / "a", reverse=False)
        assert store.ambiguous_partitions() == [("cik:1", "Revenue", date(2025, 12, 31), 2)]

    def test_an_unambiguous_store_reports_nothing(self, tmp_path: Path) -> None:
        assert _populated(tmp_path).ambiguous_partitions() == []


class TestReclaim:
    """`CHECKPOINT` frees blocks inside the file; it does not shrink it.

    After the live compaction the database held 6 used blocks out of
    4646 — 1.5 MB of data in an 859 MB file — while reporting that 12.7
    million facts had been moved out. A compaction that cannot show the
    space it freed has not finished the job it was asked to do.
    """

    @staticmethod
    def _grown(tmp_path: Path, rows: int = 5_000) -> DuckStore:
        """A store whose file has actually been written out.

        Eight facts cannot show this: DuckDB's minimum allocation is
        larger than they occupy, so the file is at its floor before
        compaction and `reclaim` has nothing to return. Without an
        explicit checkpoint the rows sit in the write-ahead log and the
        file stays at 0.01 MB, which fails to reproduce the condition
        just as thoroughly.
        """
        store = DuckStore(tmp_path / "t.db")
        record = _provenance("aold")
        store.write_provenance([record])
        store.write_facts(
            [_fact(f"cik:{i}", "Revenue", float(i), OLD, record.id) for i in range(rows)]
        )
        store._conn.execute("CHECKPOINT")
        return store

    def test_the_file_shrinks_and_the_rows_survive(self, tmp_path: Path) -> None:
        store = self._grown(tmp_path)
        path = tmp_path / "t.db"
        grown = path.stat().st_size

        store.compact(before=CUTOFF)
        assert path.stat().st_size == grown, (
            "CHECKPOINT appears to free disk space; if that is now true, "
            "reclaim may be unnecessary — check before deleting it"
        )

        before, after = store.reclaim()
        assert after < before, f"file did not shrink: {before} -> {after}"

        reopened = DuckStore(path)
        assert reopened.fact_count() == 5_000
        first = reopened.read(TUID("cik:4999"), "Revenue", as_of=NOW)
        assert [f.value for f in first] == [4999.0]
        assert reopened.provenance(first[0].provenance_id).source_system == "edgar"

    def test_reclaim_without_a_cold_tier_keeps_every_fact(self, tmp_path: Path) -> None:
        """Nothing about the rebuild is specific to compaction, and a
        rebuild that only worked on an emptied table would be a trap for
        the first person who ran it on a full one."""
        store = _populated(tmp_path)
        expected = _snapshot(store)
        store.reclaim()
        assert _snapshot(DuckStore(tmp_path / "t.db")) == expected

    def test_the_original_survives_a_failed_rebuild(self, tmp_path: Path) -> None:
        store = _populated(tmp_path)
        expected = _snapshot(store)
        temp = tmp_path / "t.db.rebuilding"
        with pytest.raises(CompactionError, match="original kept"):
            reclaim(_as_conn(_BreakOnDetach(store._conn, temp)), tmp_path / "t.db")
        assert not temp.exists(), "the rejected rebuild was left on disk"
        assert _snapshot(DuckStore(tmp_path / "t.db")) == expected


def test_compaction_leaves_provenance_reachable(tmp_path: Path) -> None:
    """Facts move; the provenance they reference does not. I1 says a
    stored fact always resolves to a provenance record, and a tiering that
    broke that would break SPTR for all of history at once."""
    store = _populated(tmp_path)
    store.compact(before=CUTOFF)
    for fact in store.subject_facts(TUID("cik:1"), as_of=NOW):
        assert store.provenance(fact.provenance_id).source_system == "edgar"


def test_the_cutoff_is_knowledge_time_not_effective_time(tmp_path: Path) -> None:
    """A fact effective long ago but learned today must stay hot — the
    tier boundary follows what the store has finished learning, not what
    the world did."""
    store = DuckStore(tmp_path / "t.db")
    record = _provenance("aold")
    store.write_provenance([record])
    store.write_facts(
        [_fact("cik:9", "Revenue", 1.0, NOW, record.id, effective_from=date(1999, 1, 1))]
    )
    assert not store.compact(before=CUTOFF).moved_anything
    hot = store._conn.execute("SELECT count(*) FROM facts").fetchone()
    assert hot is not None and hot[0] == 1


def test_cutoff_boundary_is_exclusive(tmp_path: Path) -> None:
    """A fact known exactly at the cutoff stays hot."""
    store = DuckStore(tmp_path / "t.db")
    record = _provenance("aold")
    store.write_provenance([record])
    store.write_facts([_fact("cik:9", "Revenue", 1.0, CUTOFF, record.id)])
    assert not store.compact(before=CUTOFF).moved_anything
    assert store.compact(before=CUTOFF + timedelta(microseconds=1)).rows_moved == 1
    assert store.read(TUID("cik:9"), "Revenue", as_of=NOW)[0].value == 1.0


def test_the_copy_and_the_delete_agree_on_the_boundary(tmp_path: Path) -> None:
    """`<` versus `<=` is a free choice, but the two statements have to
    make the *same* one: a delete one microsecond wider than the copy
    destroys the row in between.

    The obvious version of this test cannot see that. Give the store only
    a fact at exactly the cutoff and nothing moves at all, so the delete
    never runs and a widened one is never exercised. It needs a second,
    older fact to make the namespace eligible — then a `<=` delete takes
    the boundary row the `<` copy left behind, and it is gone from both
    tiers.
    """
    store = DuckStore(tmp_path / "t.db")
    record = _provenance("aold")
    store.write_provenance([record])
    store.write_facts(
        [
            _fact("cik:9", "Revenue", 1.0, OLD, record.id),  # makes cik eligible
            _fact("cik:9", "Assets", 2.0, CUTOFF, record.id),  # exactly on the boundary
        ]
    )
    assert store.compact(before=CUTOFF).rows_moved == 1
    assert store.read(TUID("cik:9"), "Revenue", as_of=NOW)[0].value == 1.0
    assert store.read(TUID("cik:9"), "Assets", as_of=NOW)[0].value == 2.0, (
        "the fact on the cutoff was deleted without being copied"
    )
    assert store.fact_count() == 2


def test_the_temp_suffix_is_a_name_the_glob_cannot_match(tmp_path: Path) -> None:
    """What keeps a partial file out of the view is its name, so the name
    is what gets pinned. A suffix of `.compacting.parquet` instead of
    `.parquet.compacting` would match `*.parquet` and put a truncated
    partition into every read — with nothing else in the module left to
    stop it."""
    cold = tmp_path / "cold"
    cold.mkdir()
    (cold / f"cik.parquet{_TEMP_SUFFIX}").write_bytes(b"partial")
    (cold / "cik.parquet").write_bytes(b"complete")
    assert [p.name for p in cold_files(cold)] == ["cik.parquet"]
