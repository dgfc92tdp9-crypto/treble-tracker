"""Append-only ingest log — invariant I5 (CLAUDE.md §1, spec §8.2).

One row per adapter run: which payload arrived, from which source, when, and
which parser version applies. Replaying the log through the (pure) parsers
reconstructs any past state exactly. The log exposes append and read only —
there is no update or delete, by construction.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, ConfigDict, field_validator

from treble.store.payloads import PayloadHash

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_log (
    seq            BIGINT PRIMARY KEY,
    source         VARCHAR NOT NULL,
    payload_hash   VARCHAR NOT NULL,
    source_uri     VARCHAR NOT NULL,
    fetched_at     TIMESTAMPTZ NOT NULL,
    parser_version VARCHAR NOT NULL,
    parse_config   VARCHAR
);
"""

#: Added after a whole-log replay found three adapters whose `parse` read
#: configuration passed to `__init__` — a CIK filter, an ISIN filter, a map
#: of acceptance times. The log recorded which bytes arrived and which
#: parser version applied, and that was not enough: the same payload and the
#: same parser produced different facts depending on arguments nobody wrote
#: down, so the store was not reproducible from the log (ADR-0009).
#:
#: Nullable, and nullable on purpose. The 488 rows written before this
#: column existed have no configuration to recover, and a NOT NULL default
#: of `{}` would make "we never recorded this" indistinguishable from "this
#: adapter needs nothing" — which is exactly the ambiguity that let the
#: problem run for a year. `None` means unrecorded and replay says so.
_MIGRATIONS = ("ALTER TABLE ingest_log ADD COLUMN IF NOT EXISTS parse_config VARCHAR",)


class IngestLogEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int
    source: str
    payload_hash: PayloadHash
    source_uri: str  # original request URI — replay must reproduce provenance exactly (I5)
    fetched_at: datetime
    parser_version: str
    #: Configuration `parse` reads beyond the payload itself, or None if the
    #: run predates this column. `{}` and `None` are different answers: the
    #: first says the adapter needed nothing, the second says nobody asked.
    parse_config: dict[str, Any] | None = None

    @field_validator("fetched_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware")
        # Canonical UTC: replayed entries must serialise identically (I5).
        return v.astimezone(UTC)


class IngestLog:
    """DuckDB-backed append-only log."""

    def __init__(self, db_path: Path | str) -> None:
        self._conn = duckdb.connect(str(db_path))
        self._conn.execute(_SCHEMA)
        # Applied after the CREATE, so an existing log gains the column
        # without being rewritten. `ADD COLUMN IF NOT EXISTS` makes this
        # idempotent, which matters because it runs on every open.
        for statement in _MIGRATIONS:
            self._conn.execute(statement)

    def append(
        self,
        *,
        source: str,
        payload_hash: PayloadHash,
        source_uri: str,
        fetched_at: datetime,
        parser_version: str,
        parse_config: Mapping[str, Any] | None = None,
    ) -> IngestLogEntry:
        if fetched_at.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware")
        row = self._conn.execute(
            """
            INSERT INTO ingest_log
            SELECT coalesce(max(seq), 0) + 1, ?, ?, ?, ?, ?, ? FROM ingest_log
            RETURNING seq, source, payload_hash, source_uri, fetched_at, parser_version,
                      parse_config
            """,
            [
                source,
                payload_hash,
                source_uri,
                fetched_at,
                parser_version,
                None if parse_config is None else json.dumps(parse_config, sort_keys=True),
            ],
        ).fetchone()
        if row is None:
            raise RuntimeError("ingest_log INSERT..RETURNING produced no row")
        return self._entry(row)

    def read(self, *, up_to_seq: int | None = None) -> list[IngestLogEntry]:
        """Entries in order, optionally up to a sequence point (for replay-to-a-moment)."""
        if up_to_seq is None:
            rows = self._conn.execute("SELECT * FROM ingest_log ORDER BY seq").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM ingest_log WHERE seq <= ? ORDER BY seq", [up_to_seq]
            ).fetchall()
        return [self._entry(r) for r in rows]

    @staticmethod
    def _entry(row: tuple[object, ...]) -> IngestLogEntry:
        seq, source, ph, source_uri, fetched_at, parser_version, parse_config = row
        return IngestLogEntry(
            seq=int(seq),  # type: ignore[call-overload]
            source=str(source),
            payload_hash=PayloadHash(str(ph)),
            source_uri=str(source_uri),
            fetched_at=fetched_at,
            parser_version=str(parser_version),
            parse_config=None if parse_config is None else json.loads(str(parse_config)),
        )
