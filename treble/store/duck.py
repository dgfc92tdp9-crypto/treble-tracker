"""DuckDB implementation of Store/HistoryStore (CLAUDE.md §2, §3).

Enforces at the storage boundary:

- I1 — a fact cannot be written unless its provenance record exists
  (NOT NULL column plus an explicit existence check per batch).
- I2 — inserts only; the only temporal predicate offered is ``as_of`` on
  knowledge time with latest-knowledge-wins resolution. There is no UPDATE
  or DELETE statement anywhere in this module. Compaction moves rows
  between tiers and therefore does delete from the hot table — that lives
  in :mod:`treble.store.cold`, off the protocol, and only ever after the
  same rows have been written and verified elsewhere. No fact ever stops
  being visible.

Values are stored in typed columns (numeric/int/text/bool/date) so DuckDB
queries — and later TQL plans — operate natively rather than through JSON.

Facts live in two tiers. New ones are inserted into the ``facts`` table;
settled history is compacted out to sorted Parquet by :mod:`treble.store.cold`
and read back through the ``all_facts`` view, which every query below goes
through. The tiering is physical only — no read can tell which tier a fact
came from, and none of them may try.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import duckdb
import pyarrow as pa

from treble.core.facts import Fact, FactValue
from treble.core.identifiers import TUID
from treble.core.provenance import Provenance, ProvenanceId
from treble.store.cold import (
    CompactionReport,
    cold_dir_for,
    compact,
    union_sql,
)
from treble.store.cold import (
    reclaim as reclaim_database,
)
from treble.store.schema import (
    FACT_ARROW_SCHEMA,
    FACT_COLUMNS,
    FACT_PROJECTION,
    SCHEMA,
    TIE_BREAK,
)

#: The union of both tiers. Queries name this, never `facts` — reading the
#: table directly would silently answer from the hot tier alone, which
#: after a compaction is a store that has forgotten most of its history
#: while still returning rows for the part it kept.
_ALL = "all_facts"

# Latest knowledge wins per effective period, visible as of the knowledge date.
#
# This is also what makes the cold tier crash-safe: a row present in both
# tiers shares a partition with its own copy, so `rn = 1` discards the
# duplicate. See `cold.py` — the whole compaction ordering depends on it.
_VISIBLE_TEMPLATE = """
SELECT * EXCLUDE (rn) FROM (
    SELECT *, row_number() OVER (
        PARTITION BY subject, field, effective_from, coalesce(effective_to, DATE '9999-12-31')
        ORDER BY {tie_break}
    ) AS rn
    FROM {table}
    WHERE subject = ? AND field = ? AND knowledge_from <= ?
) WHERE rn = 1
"""

# Interpolated on its own line rather than as an f-string above, because a
# lint-suppression comment placed beside the opening triple quote lands
# *inside* the string and gets sent to DuckDB as part of the query. That
# happened here; the SQL began with a comment and the constant was silently
# wrong until it was printed.
_VISIBLE = _VISIBLE_TEMPLATE.format(table=_ALL, tie_break=TIE_BREAK)

#: The same window keyed on subject alone, for the two reads that want
#: every field. Shared with `_VISIBLE_TEMPLATE` through `TIE_BREAK` rather
#: than restated, because these three windows must resolve visibility
#: identically and previously did so only by having been copied.
_BY_SUBJECT = """
    SELECT {columns}, row_number() OVER (
        PARTITION BY subject, field, effective_from,
                     coalesce(effective_to, DATE '9999-12-31')
        ORDER BY {tie_break}
    ) AS rn
    FROM {table}
    WHERE subject = ? AND knowledge_from <= ?
""".format(columns="{columns}", tie_break=TIE_BREAK, table=_ALL)  # noqa: S608


class MissingProvenanceError(Exception):
    """A fact references a provenance id that has not been stored (I1)."""


def _decompose(
    value: FactValue,
) -> tuple[str, float | None, int | None, str | None, bool | None, date | None]:
    # bool must precede int: bool is a subclass of int in Python.
    if value is None:
        return "null", None, None, None, None, None
    if isinstance(value, bool):
        return "bool", None, None, None, value, None
    if isinstance(value, int):
        return "int", None, value, None, None, None
    if isinstance(value, float):
        return "num", value, None, None, None, None
    if isinstance(value, date):
        return "date", None, None, None, None, value
    return "text", None, None, str(value), None, None


def _recompose(
    kind: str,
    num: float | None,
    intv: int | None,
    text: str | None,
    boolean: bool | None,
    day: date | None,
) -> FactValue:
    match kind:
        case "null":
            return None
        case "num":
            return num
        case "int":
            return intv
        case "text":
            return text
        case "bool":
            return boolean
        case "date":
            return day
    raise ValueError(f"unknown value kind in store: {kind!r}")


class DuckStore:
    """Store + HistoryStore over a single DuckDB database file."""

    def __init__(self, db_path: Path | str, *, cold_dir: Path | None = None) -> None:
        self._path = Path(db_path).resolve()
        self._cold_dir = (cold_dir or cold_dir_for(self._path)).resolve()
        self._conn = duckdb.connect(str(self._path))
        self._conn.execute(SCHEMA)
        self._refresh_view()

    def _refresh_view(self) -> None:
        """Rebuild `all_facts` over the cold partitions currently on disk.

        A TEMP view, rebuilt at every connect, rather than a persistent
        one. A persistent view would bake today's absolute paths into the
        database file, so moving the data directory — which
        `TREBLE_DATA_DIR` exists to allow, and which the storage
        measurements recommend doing onto an external disk — would leave
        the store pointing at partitions that are no longer there. The
        view costs microseconds to recreate and cannot go stale.
        """
        self._conn.execute(f"CREATE OR REPLACE TEMP VIEW {_ALL} AS {union_sql(self._cold_dir)}")

    @property
    def path(self) -> Path:
        """Where the database file is.

        Exposed because callers that need a *data directory* — the PEOP
        directory, the vault — should derive it from the store they were
        given rather than be told twice and given the chance to disagree.
        """
        return self._path

    @property
    def cold_dir(self) -> Path:
        return self._cold_dir

    # -- maintenance ----------------------------------------------------------

    def compact(
        self, *, before: datetime, namespaces: tuple[str, ...] | None = None
    ) -> CompactionReport:
        """Move facts settled before ``before`` into the cold tier.

        Not on the `Store` protocol, and deliberately: compaction changes
        where bytes live, never which facts are visible, so it is
        maintenance rather than a store operation. Putting it on the
        protocol would also put a deletion on an interface whose entire
        purpose (I2) is not offering one.
        """
        report = compact(self._conn, self._cold_dir, before=before, namespaces=namespaces)
        if report.moved_anything:
            self._refresh_view()
            # Flushes the delete to the file and puts the blocks on the
            # free list so later writes reuse them. It does **not** return
            # them to the filesystem: measured, the file is byte-identical
            # before and after this call, and the live store sat at 859 MB
            # holding 6 used blocks out of 4646. Call `reclaim` for that.
            self._conn.execute("CHECKPOINT")
        return report

    def reclaim(self) -> tuple[int, int]:
        """Return the space compaction freed to the filesystem.

        Separate from :meth:`compact` because it replaces the database
        file underneath the connection, which is a different kind of risk
        from moving rows between tiers and deserves to be asked for
        explicitly. Reopens afterwards, so the store stays usable.

        Returns (bytes before, bytes after).
        """
        before, after = reclaim_database(self._conn, self._path)
        self._conn = duckdb.connect(str(self._path))
        self._conn.execute(SCHEMA)
        self._refresh_view()
        return before, after

    # -- writes (insert-only) -------------------------------------------------

    def write_provenance(self, records: list[Provenance]) -> None:
        # Deliberately row-at-a-time, unlike `write_facts` below.
        #
        # That method was rewritten to a single Arrow batch after its loop
        # was measured at 11.2s for one EDGAR payload. The obvious next move
        # is to do the same here, and the measurement says not to: the live
        # store holds 8,107,326 facts against 277 provenance records, a
        # ratio of 29,268:1. The loop costs 277 statements across the whole
        # history of the install.
        #
        # A bulk version would also have to reproduce the dedup below as an
        # anti-join, which is more to get wrong than the microseconds are
        # worth. Optimising this would be optimising on the shape of the
        # neighbouring bug rather than on a measurement.
        for record in records:
            # Content-addressed id: re-inserting an identical record is a no-op.
            self._conn.execute(
                """
                INSERT INTO provenance
                SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                WHERE NOT EXISTS (SELECT 1 FROM provenance WHERE id = ?)
                """,
                [
                    record.id,
                    record.source_system,
                    record.source_uri,
                    record.retrieved_at,
                    record.method.value,
                    record.extractor_version,
                    record.confidence,
                    record.locator,
                    record.payload_hash,
                    list(record.input_ids),
                    record.id,
                ],
            )

    def write_facts(self, facts: list[Fact]) -> None:
        # First, not last. This guard existed below the provenance check,
        # which builds `WHERE id IN ()` from an empty set and is a DuckDB
        # syntax error. An empty batch is legitimate — the DTCC tape
        # publishes nothing on a weekend — so a source with no data for a
        # day took down the whole refresh with a parser error, which reads
        # like a corrupted store rather than a quiet Saturday.
        if not facts:
            return
        needed = {f.provenance_id for f in facts}
        placeholders = ",".join("?" for _ in needed)
        rows = self._conn.execute(
            # Only `?` placeholders are interpolated; all values are bound.
            f"SELECT id FROM provenance WHERE id IN ({placeholders})",  # noqa: S608
            list(needed),
        ).fetchall()
        missing = needed - {r[0] for r in rows}
        if missing:
            raise MissingProvenanceError(
                f"facts reference unknown provenance ids: {sorted(missing)[:3]}"
            )

        # One Arrow batch, one INSERT. The row-at-a-time loop this replaced
        # cost 11.2s for a single EDGAR companyfacts payload (37,540 facts)
        # against 0.5s here — a 21x difference, measured. Each `execute` was
        # a full round trip through DuckDB's parser, and the cost fell on
        # every `treble populate` as much as on the test suite.
        #
        # The table's ART index accounts for 0.10s of that and is left in
        # place: dropping and recreating it around each write saves less
        # than it risks, since a crash mid-load would leave the store
        # without the index every read depends on.
        columns: dict[str, list[object]] = {name: [] for name in FACT_COLUMNS}
        for fact in facts:
            kind, num, intv, text, boolean, day = _decompose(fact.value)
            columns["subject"].append(fact.subject)
            columns["field"].append(fact.field)
            columns["value_kind"].append(kind)
            columns["value_num"].append(num)
            columns["value_int"].append(intv)
            columns["value_text"].append(text)
            columns["value_bool"].append(boolean)
            columns["value_date"].append(day)
            columns["effective_from"].append(fact.effective_from)
            columns["effective_to"].append(fact.effective_to)
            columns["knowledge_from"].append(fact.knowledge_from)
            columns["provenance_id"].append(fact.provenance_id)

        # The schema is explicit rather than inferred. A batch whose values
        # are all numeric leaves `value_text` entirely null, and Arrow would
        # infer the null type for it — which then fails or silently coerces
        # on insert depending on the column. Stating the types means a batch
        # of one kind of value writes exactly like a batch of many.
        batch = pa.table(
            {
                name: pa.array(values, type=FACT_ARROW_SCHEMA.field(name).type)
                for name, values in columns.items()
            },
            schema=FACT_ARROW_SCHEMA,
        )
        self._conn.register("_incoming_facts", batch)
        try:
            self._conn.execute(
                f"INSERT INTO facts ({', '.join(FACT_COLUMNS)}) "  # noqa: S608
                f"SELECT {', '.join(FACT_COLUMNS)} FROM _incoming_facts"
            )
        finally:
            # Unregister even on failure: a leftover view would shadow the
            # next write's batch and silently insert the previous one again.
            self._conn.unregister("_incoming_facts")

    # -- reads (as_of is required; I2) ---------------------------------------

    def has_subject(self, subject: TUID) -> bool:
        """Whether the store holds any fact for this subject.

        Used to reject an unknown identifier at resolution time. Without it
        a mistyped CUSIP resolves happily and every cell renders as a dash —
        indistinguishable from a real instrument with no data.
        """
        row = self._conn.execute(
            "SELECT 1 FROM all_facts WHERE subject = ? LIMIT 1", [subject]
        ).fetchone()
        return row is not None

    def subject_provenance(self, subject: TUID, *, as_of: datetime) -> list[ProvenanceId]:
        """Distinct provenance ids behind a subject's visible values (I1, I2).

        Backs SPTR. It asks the store what is actually there rather than
        iterating the field dictionary: almost every real value is under an
        as-reported XBRL tag, which the dictionary resolves dynamically and
        therefore cannot enumerate. Walking the dictionary found nothing at
        all for a company with 345k facts — an empty trace that read as "no
        sources" rather than "wrong question".

        Point-in-time like every other read: latest knowledge wins, nothing
        known after ``as_of`` is visible.
        """
        rows = self._conn.execute(
            "SELECT DISTINCT provenance_id FROM ("  # noqa: S608
            + _BY_SUBJECT.format(columns="provenance_id")
            + ") WHERE rn = 1",
            [subject, as_of],
        ).fetchall()
        return [ProvenanceId(row[0]) for row in rows if row[0] is not None]

    def ambiguous_partitions(self, *, limit: int = 20) -> list[tuple[str, str, date, int]]:
        """Where one key holds several values and only one can be shown.

        A partition — subject, field, effective period, knowledge time —
        is assumed to identify a single value. These hold more than one,
        so `rn = 1` picks and the rest are invisible.

        On the live store there are 6,766 of these against 8.9 million
        visible facts, and the cause is a modelling gap rather than bad
        data: 5,996 are GLEIF relationship records, where an entity may
        legitimately hold several at once, and 367 are `edgar:filing:form`
        for filers who submitted more than one form on a day. The key is
        wrong for those fields, not the sources.

        Reported rather than repaired, because the repair is per-field —
        those need a discriminator in the subject or the field — and a
        store that quietly collapsed them would be hiding data it holds.

        Returns (subject, field, effective_from, distinct value count).
        """
        rows = self._conn.execute(
            """
            SELECT subject, field, effective_from,
                   count(DISTINCT coalesce(value_num::VARCHAR, value_int::VARCHAR,
                                           value_text, value_bool::VARCHAR,
                                           value_date::VARCHAR, 'null')) AS values
            FROM all_facts
            GROUP BY subject, field, effective_from,
                     coalesce(effective_to, DATE '9999-12-31'), knowledge_from
            HAVING values > 1
            ORDER BY values DESC, subject, field
            LIMIT ?
            """,
            [limit],
        ).fetchall()
        return [(str(r[0]), str(r[1]), r[2], int(r[3])) for r in rows]

    def fact_count(self) -> int:
        """How many facts the store holds.

        Cheap enough to call at startup, which is the point: an empty store
        renders every bound cell as a dash, and a dash is indistinguishable
        from "not reported". The clients check this so an unpopulated store
        announces itself instead of looking like a company with no figures.

        Counts stored rows across both tiers, not distinct facts. A
        compaction interrupted between its rename and its delete leaves
        rows in both tiers, and this will count those twice until the next
        run collapses them — visible only here, because every other read
        resolves the duplicate away.
        """
        row = self._conn.execute("select count(*) from all_facts").fetchone()
        return int(row[0]) if row else 0

    def subjects_with_prefix(self, prefix: str, *, as_of: datetime) -> list[TUID]:
        """Subjects in a namespace, sorted.

        Backs TQL universe selection: `bonds(...)` is every `cusip:` subject
        the store holds, narrowed by the query's predicates. Sorted so a
        query returns rows in the same order twice — an unordered result
        would make two runs of one query look like different answers.

        Point-in-time like every other read (I2): a subject nothing was
        known about at ``as_of`` is not in the universe at ``as_of``.
        Including it would return a row of nulls for an instrument that did
        not yet exist.
        """
        rows = self._conn.execute(
            "SELECT DISTINCT subject FROM all_facts "
            "WHERE starts_with(subject, ?) AND knowledge_from <= ? ORDER BY subject",
            [prefix, as_of],
        ).fetchall()
        return [TUID(row[0]) for row in rows]

    def subjects_with_value(self, field: str, value: str, *, as_of: datetime) -> list[TUID]:
        """Subjects asserting `value` for `field` — the reverse of a read.

        Facts are keyed by subject, so every other read here starts from
        one. Some questions run the other way: *who* claims this LEI as a
        parent, *which* instruments map to this CUSIP. Answering those by
        walking subjects costs one read each, and the entity graph made
        that concrete — 373,125 LEI subjects, so `children_of` appeared to
        hang and had to refuse rather than finish.

        It does not need to. The rows are in one table and DuckDB will
        answer this with a single scan of a column it already holds; the
        expensive version was a Python loop, not a missing index. This is
        the same query the loop was approximating, run where the data is.

        Point-in-time like every other read (I2), and sorted so two runs of
        one question return the same order.
        """
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        rows = self._conn.execute(
            "SELECT DISTINCT subject FROM all_facts "
            "WHERE field = ? AND value_text = ? AND knowledge_from <= ? "
            "ORDER BY subject",
            [field, value, as_of],
        ).fetchall()
        return [TUID(row[0]) for row in rows]

    def read(self, subject: TUID, field: str, *, as_of: datetime) -> list[Fact]:
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        rows = self._conn.execute(
            _VISIBLE + " ORDER BY effective_from", [subject, field, as_of]
        ).fetchall()
        return [self._fact(r) for r in rows]

    def history(
        self,
        subject: TUID,
        field: str,
        *,
        as_of: datetime,
        effective_from: date | None = None,
        effective_to: date | None = None,
    ) -> list[Fact]:
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        query = _VISIBLE
        params: list[object] = [subject, field, as_of]
        if effective_from is not None:
            query += " AND effective_from >= ?"
            params.append(effective_from)
        if effective_to is not None:
            query += " AND effective_from <= ?"
            params.append(effective_to)
        rows = self._conn.execute(query + " ORDER BY effective_from", params).fetchall()
        return [self._fact(r) for r in rows]

    def subject_facts(self, subject: TUID, *, as_of: datetime) -> list[Fact]:
        """Every visible fact for a subject, across all fields.

        Backs bulk export (spec §8.5), where the caller wants a whole
        namespace rather than one field. Same latest-knowledge-wins window
        as :meth:`history` and the same required ``as_of`` (I2) — a bulk
        transport that could see facts the screen path could not would be a
        second source of truth wearing a protocol as a disguise.
        """
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        rows = self._conn.execute(
            "SELECT * EXCLUDE (rn) FROM ("  # noqa: S608
            + _BY_SUBJECT.format(columns=FACT_PROJECTION)
            + ") WHERE rn = 1 ORDER BY field, effective_from",
            [subject, as_of],
        ).fetchall()
        return [self._fact(r) for r in rows]

    def provenance(self, provenance_id: ProvenanceId) -> Provenance:
        row = self._conn.execute(
            "SELECT * FROM provenance WHERE id = ?", [provenance_id]
        ).fetchone()
        if row is None:
            raise KeyError(provenance_id)
        (
            _,
            source_system,
            source_uri,
            retrieved_at,
            method,
            extractor_version,
            confidence,
            locator,
            payload_hash,
            input_ids,
        ) = row
        return Provenance(
            source_system=source_system,
            source_uri=source_uri,
            retrieved_at=retrieved_at,
            method=method,
            extractor_version=extractor_version,
            confidence=confidence,
            locator=locator,
            payload_hash=payload_hash,
            input_ids=tuple(ProvenanceId(i) for i in input_ids),
        )

    @staticmethod
    def _fact(row: tuple[object, ...]) -> Fact:
        (subject, field, kind, num, intv, text, boolean, day, eff_from, eff_to, k_from, pid) = row
        return Fact(
            subject=TUID(str(subject)),
            field=str(field),
            value=_recompose(str(kind), num, intv, text, boolean, day),  # type: ignore[arg-type]
            effective_from=eff_from,
            effective_to=eff_to,
            knowledge_from=k_from,
            provenance_id=ProvenanceId(str(pid)),
        )
