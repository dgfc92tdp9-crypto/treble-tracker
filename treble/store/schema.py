"""The fact table's shape, in one place.

The DDL, the column order and the Arrow types must agree, and until the
cold tier existed proximity inside ``duck.py`` was what enforced that. A
second reader of the same rows — Parquet files written by ``cold.py`` and
unioned back in — makes proximity insufficient: a column added to the DDL
without being added to the projection would produce a view whose columns
line up by position with the *wrong* names, which is the failure the
explicit column list in ``write_facts`` already exists to prevent.

So all three live here and are imported by both tiers. There is exactly one
statement of what a fact row is.
"""

from __future__ import annotations

import pyarrow as pa

SCHEMA = """
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

#: Fact columns, in table order. Named explicitly everywhere rather than
#: relying on `SELECT *`, so a column added to the table in a later
#: migration cannot silently shift every value one place to the left.
FACT_COLUMNS = (
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
FACT_ARROW_SCHEMA = pa.schema(
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

#: The projection used everywhere a fact row is selected. Positional
#: unpacking in `DuckStore._fact` depends on this order, so it is stated
#: rather than left to whatever `SELECT *` happens to return from a table
#: or a Parquet file.
FACT_PROJECTION = ", ".join(FACT_COLUMNS)

#: Ordering for the latest-knowledge-wins window.
#:
#: `knowledge_from DESC` alone is not a total order, and the live store
#: proved it: **6,766 (subject, field, effective period, knowledge time)
#: keys held more than one distinct value** when this was written — 7,417
#: today, against 10.3 million visible facts — so `row_number() ... WHERE
#: rn = 1` was picking whichever row the storage engine returned first.
#:
#: That was invisible until the cold tier reordered rows on disk and the
#: visible fact set came back with an identical row count and a different
#: hash. Nothing was lost — the compaction verifies every row it moves —
#: but the store had been answering some questions by accident, and two
#: runs of one query could disagree. `subjects_with_prefix` already sorts
#: for exactly this reason; the visibility window needed the same.
#:
#: These are **not contradictions in the sources**, but they are not all
#: one thing either, and reading them as one thing cost a wrong fix.
#:
#: Some genuinely are multi-valued facts under a key that assumes one
#: value: `edgar:filing:form` (367 today) because a filer can submit an
#: 8-K and a Form 4 on the same day, and `nport:curCd` (643), the largest
#: of them. Every value is stored; the window shows one; the remedy is a
#: key that admits several.
#:
#: The GLEIF relationship rows were **not** that, though they were counted
#: as if they were. One relationship record was written as two facts — the
#: counterparty under `gleif:rr:<TYPE>` and its status under
#: `gleif:rr:<TYPE>:status` — so an entity holding a current record and a
#: superseded one put two values in each key, and this ordering then chose
#: each *independently*: the counterparty by `value_text`, the status by
#: the null-ranking term below. The pair that came back was an assertion
#: the source never made, and on the live store it named annulled parents
#: as current ones. Nothing was multi-valued; one record had been taken
#: apart. Re-keyed by counterparty
#: (`core.entity_graph.relationship_state_field`), 3 partitions remain,
#: all genuine duplicate filings naming the same counterparty.
#:
#: The 6,078 `gleif:rr:*` partitions still counted here are the
#: superseded two-fact encoding, kept because nothing is deleted from this
#: store (I2) and read by nothing.
#:
#: The extra columns make the order total. They do not make the answer
#: *right* where the key is genuinely wrong for a field — no ordering can
#: — they make it the same every time. `DuckStore.ambiguous_partitions`
#: exists so the affected fields can be found and remodelled rather than
#: silently collapsed. **Its count is a prompt to look, not a diagnosis:
#: the two causes above need opposite remedies.**
#: A stated value outranks a null at the same knowledge time. This term
#: is second, immediately after knowledge time, and it is not cosmetic:
#: `value_kind` ascending puts the literal string `'null'` before
#: `'text'`, so without it the *absence* of information outranked the
#: information.
#:
#: Found by fixing the N-PORT currency extractor. Re-parsing the stored
#: payloads produced `AED` for a holding that had been stored as null,
#: both facts carrying the same knowledge time — correctly, because
#: re-parsing corrects our reading of a source rather than learning
#: something new about the world — and the read kept returning null. Four
#: rows in one partition: one `AED`, three nulls, and the nulls won.
_VALUE_BEFORE_NULL = "value_kind = 'null'"

TIE_BREAK = (
    f"knowledge_from DESC, {_VALUE_BEFORE_NULL}, provenance_id DESC, value_kind, "
    "value_num, value_int, value_text, value_bool, value_date"
)

__all__ = [
    "FACT_ARROW_SCHEMA",
    "FACT_COLUMNS",
    "FACT_PROJECTION",
    "SCHEMA",
    "TIE_BREAK",
]
