"""DuckDB implementation of Store/HistoryStore (CLAUDE.md §2, §3).

Enforces at the storage boundary:

- I1 — a fact cannot be written unless its provenance record exists
  (NOT NULL column plus an explicit existence check per batch).
- I2 — inserts only; the only temporal predicate offered is ``as_of`` on
  knowledge time with latest-knowledge-wins resolution. There is no UPDATE
  or DELETE statement anywhere in this module.

Values are stored in typed columns (numeric/int/text/bool/date) so DuckDB
queries — and later TQL plans — operate natively rather than through JSON.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import duckdb
import pyarrow as pa

from treble.core.facts import Fact, FactValue
from treble.core.identifiers import TUID
from treble.core.provenance import Provenance, ProvenanceId

_SCHEMA = """
CREATE TABLE IF NOT EXISTS provenance (
    id                VARCHAR PRIMARY KEY,
    source_system     VARCHAR NOT NULL,
    source_uri        VARCHAR NOT NULL,
    retrieved_at      TIMESTAMPTZ NOT NULL,
    method            VARCHAR NOT NULL,
    extractor_version VARCHAR NOT NULL,
    confidence        DOUBLE NOT NULL,
    locator           VARCHAR,
    payload_hash      VARCHAR,
    input_ids         VARCHAR[] NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    subject        VARCHAR NOT NULL,
    field          VARCHAR NOT NULL,
    value_kind     VARCHAR NOT NULL,
    value_num      DOUBLE,
    value_int      BIGINT,
    value_text     VARCHAR,
    value_bool     BOOLEAN,
    value_date     DATE,
    effective_from DATE NOT NULL,
    effective_to   DATE,
    knowledge_from TIMESTAMPTZ NOT NULL,
    provenance_id  VARCHAR NOT NULL
);
CREATE INDEX IF NOT EXISTS facts_read_idx
    ON facts (subject, field, effective_from, knowledge_from);
"""

#: Fact columns, in table order. Named explicitly in the bulk INSERT rather
#: than relying on `SELECT *`, so a column added to the table in a later
#: migration cannot silently shift every value one place to the left.
_FACT_COLUMNS = (
    "subject",
    "field",
    "value_kind",
    "value_num",
    "value_int",
    "value_text",
    "value_bool",
    "value_date",
    "effective_from",
    "effective_to",
    "knowledge_from",
    "provenance_id",
)

#: Arrow types matching the `facts` DDL above. Kept adjacent to it because
#: the two must agree and nothing but proximity and the round-trip tests
#: enforces that.
_FACT_ARROW_SCHEMA = pa.schema(
    [
        pa.field("subject", pa.string()),
        pa.field("field", pa.string()),
        pa.field("value_kind", pa.string()),
        pa.field("value_num", pa.float64()),
        pa.field("value_int", pa.int64()),
        pa.field("value_text", pa.string()),
        pa.field("value_bool", pa.bool_()),
        pa.field("value_date", pa.date32()),
        pa.field("effective_from", pa.date32()),
        pa.field("effective_to", pa.date32()),
        pa.field("knowledge_from", pa.timestamp("us", tz="UTC")),
        pa.field("provenance_id", pa.string()),
    ]
)

# Latest knowledge wins per effective period, visible as of the knowledge date.
_VISIBLE = """
SELECT * EXCLUDE (rn) FROM (
    SELECT *, row_number() OVER (
        PARTITION BY subject, field, effective_from, coalesce(effective_to, DATE '9999-12-31')
        ORDER BY knowledge_from DESC
    ) AS rn
    FROM facts
    WHERE subject = ? AND field = ? AND knowledge_from <= ?
) WHERE rn = 1
"""


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

    def __init__(self, db_path: Path | str) -> None:
        self._conn = duckdb.connect(str(db_path))
        self._conn.execute(_SCHEMA)

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
        columns: dict[str, list[object]] = {name: [] for name in _FACT_COLUMNS}
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
                name: pa.array(values, type=_FACT_ARROW_SCHEMA.field(name).type)
                for name, values in columns.items()
            },
            schema=_FACT_ARROW_SCHEMA,
        )
        self._conn.register("_incoming_facts", batch)
        try:
            self._conn.execute(
                f"INSERT INTO facts ({', '.join(_FACT_COLUMNS)}) "  # noqa: S608
                f"SELECT {', '.join(_FACT_COLUMNS)} FROM _incoming_facts"
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
            "SELECT 1 FROM facts WHERE subject = ? LIMIT 1", [subject]
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
            """
            SELECT DISTINCT provenance_id FROM (
                SELECT provenance_id, row_number() OVER (
                    PARTITION BY subject, field, effective_from,
                                 coalesce(effective_to, DATE '9999-12-31')
                    ORDER BY knowledge_from DESC
                ) AS rn
                FROM facts
                WHERE subject = ? AND knowledge_from <= ?
            ) WHERE rn = 1
            """,
            [subject, as_of],
        ).fetchall()
        return [ProvenanceId(row[0]) for row in rows if row[0] is not None]

    def fact_count(self) -> int:
        """How many facts the store holds.

        Cheap enough to call at startup, which is the point: an empty store
        renders every bound cell as a dash, and a dash is indistinguishable
        from "not reported". The clients check this so an unpopulated store
        announces itself instead of looking like a company with no figures.
        """
        row = self._conn.execute("select count(*) from facts").fetchone()
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
            "SELECT DISTINCT subject FROM facts "
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
            "SELECT DISTINCT subject FROM facts "
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
            """
            SELECT * EXCLUDE (rn) FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY subject, field, effective_from,
                                 coalesce(effective_to, DATE '9999-12-31')
                    ORDER BY knowledge_from DESC
                ) AS rn
                FROM facts
                WHERE subject = ? AND knowledge_from <= ?
            ) WHERE rn = 1
            ORDER BY field, effective_from
            """,
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
