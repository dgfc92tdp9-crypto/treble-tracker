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
        for fact in facts:
            kind, num, intv, text, boolean, day = _decompose(fact.value)
            self._conn.execute(
                "INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    fact.subject,
                    fact.field,
                    kind,
                    num,
                    intv,
                    text,
                    boolean,
                    day,
                    fact.effective_from,
                    fact.effective_to,
                    fact.knowledge_from,
                    fact.provenance_id,
                ],
            )

    # -- reads (as_of is required; I2) ---------------------------------------

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
