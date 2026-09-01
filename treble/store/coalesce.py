"""Do not write a row to say a value has not changed.

Measured on the live store, 2026-08-30: a single `treble refresh` wrote
**505,461 fact rows to carry 22,453 rows of new information** — 95.6% of the
batch restated values the store already held, unchanged, from the same
source. `fred:BAMLC0A0CM PX_LAST` for 2025-09-03 was present eight times,
value `0.81` in all eight, once per refresh since 2026-07-27.

Nothing was broken. The append is what I2 asks for and `write_facts` was
doing it correctly. But the cost is **linear in refresh frequency**, and
that makes it the thing standing in front of every "update more often"
improvement anyone will ever want here:

    daily refresh    184,493,265 rows/yr   to carry 8,195,345
    hourly refresh 4,427,838,360 rows/yr   to carry 8,195,345

The second number is the same in both rows. That is the whole argument.

## Why dropping the row loses nothing

The re-fetch is still recorded — just once, in the layer built for it,
instead of once per fact:

* the **ingest log** has an append-only entry per fetch, and
* **provenance** has one record per *payload*, not per fact (586 records
  for 15 million facts on the live store).

So "we re-checked FRED on 2026-08-30 and it still said 0.81" survives
intact. What stops happening is writing that sentence 7,225 times.

## Why the row that stays is the *more* correct one

`core.facts` defines `knowledge_from` as "when the system could **first**
have known it". Under plain append, the visible row for an unchanged value
carries the time of the **latest** re-fetch — so 32% of the live store
reported a knowledge time that contradicted the field's own definition, and
`knowledge_to`, derived as the superseding row's `knowledge_from`
(ADR-0001), described seven supersessions of `0.81` by `0.81`.

Coalescing makes the store agree with its own contract. That is a
correctness result, and it is the reason this is in the write path rather
than in a cleanup script.

## What is deliberately conservative

**Only within one source.** Reads do not partition by source, so if FRED
and the ECB both assert a value, dropping the second would silently change
which source the visible row traces to — an I1 provenance change disguised
as a space saving. Corroboration from a second source is new information
and is always written.

**Only moving forward in knowledge time.** A fact arriving with a
`knowledge_from` at or before the newest row for its key is a backfill of
older knowledge, and whether it is redundant depends on what was visible
*then* rather than now. Those are written unconditionally: the saving is
not worth a rule with a case in it nobody exercises.

**Values only.** This preserves the *value* every `as_of` query returns,
exactly — that is what `tests/store/test_coalesce.py` asserts across a
generated history. It deliberately does **not** preserve `knowledge_from`
and `provenance_id` on the visible row: those move from the latest
re-fetch to the first observation, which is the correction described above.
"""

from __future__ import annotations

from treble.store.schema import TIE_BREAK

#: The columns that make an assertion the *same* assertion.
#:
#: `value_kind` is included and, as of today, **cannot change the outcome**:
#: `duck._decompose` gives every kind but `null` exactly one non-null typed
#: column, so the typed columns already separate them. Mutation testing
#: showed this — removing `value_kind` here killed no test, and no input the
#: `Fact` model can produce would make one fail.
#:
#: It stays because the property is `_decompose`'s to keep, not this
#: module's to assume, and a kind that carried its meaning *only* in the
#: kind — a redacted or restriction-withheld value, entirely plausible here
#: — would otherwise coalesce into a plain null with nothing to say so.
#: `tests/store/test_coalesce.py::TestValueKindIsImpliedToday` pins the
#: assumption, so the day it stops holding a test fails rather than a
#: comment quietly going out of date.
VALUE_COLUMNS = (
    "value_kind",
    "value_num",
    "value_int",
    "value_text",
    "value_bool",
    "value_date",
)

#: The columns identifying which assertion an incoming row restates.
#:
#: `effective_to` is compared with `IS NOT DISTINCT FROM` rather than `=`,
#: because it is nullable and null means "open-ended" — a real period, not
#: a missing one. SQL equality on two nulls is null, which is not true, so
#: `=` would treat every open-ended fact as a new assertion and coalesce
#: nothing at all for the sources that use them.
KEY_COLUMNS = ("subject", "field", "effective_from", "effective_to")


def _match(columns: tuple[str, ...], left: str, right: str) -> str:
    return " AND ".join(f"{left}.{c} IS NOT DISTINCT FROM {right}.{c}" for c in columns)


#: The newest assertion per (key, source), restricted to the subjects in the
#: batch before it is ranked. Without that restriction it ranks the whole
#: store on every write: 15 million rows to decide 7,225.
#:
#: A plain template rather than an f-string, `.format`ted on its own line
#: below. A lint-suppression comment placed beside an opening triple quote
#: lands *inside* the string and is sent to DuckDB as part of the query —
#: which `duck.py` records happening once already, and which happened again
#: here while this module was being written.
_CURRENT_TEMPLATE = """
        SELECT {key_columns}, {value_columns},
               p.source_system AS source_system,
               f.knowledge_from AS knowledge_from,
               row_number() OVER (
                   PARTITION BY subject, field, effective_from,
                                coalesce(effective_to, DATE '9999-12-31'),
                                p.source_system
                   ORDER BY {tie_break}
               ) AS rn
        FROM {all_facts} f
        JOIN provenance p ON f.provenance_id = p.id
        WHERE f.subject IN (SELECT DISTINCT subject FROM {incoming})
"""

_SCREEN_TEMPLATE = """
        WITH current AS ({current}),
             newest AS (SELECT * FROM current WHERE rn = 1),
             batch AS (
                 SELECT i.*, p.source_system AS source_system
                 FROM {incoming} i JOIN provenance p ON i.provenance_id = p.id
             )
        SELECT b.*, EXISTS (
            SELECT 1 FROM newest n
            WHERE {key_match}
              AND n.source_system = b.source_system
              AND {value_match}
              AND n.knowledge_from <= b.knowledge_from
        ) AS redundant
        FROM batch b
"""


def redundant_ids_sql(all_facts: str, incoming: str) -> str:
    """SQL marking which rows of ``incoming`` restate what is already held.

    Yields every column of ``incoming`` plus a boolean ``redundant``, so the
    caller can count and filter in one pass rather than run the comparison
    twice and risk the two disagreeing.

    Only identifiers are interpolated: two module-level column tuples,
    `TIE_BREAK` from the schema, and two table names the caller supplies as
    literals. No value reaches this string — the batch arrives as a
    registered Arrow table, never as SQL.
    """
    current = _CURRENT_TEMPLATE.format(
        key_columns=", ".join(KEY_COLUMNS),
        value_columns=", ".join(VALUE_COLUMNS),
        tie_break=TIE_BREAK,
        all_facts=all_facts,
        incoming=incoming,
    )
    return _SCREEN_TEMPLATE.format(
        current=current,
        incoming=incoming,
        key_match=_match(KEY_COLUMNS, "n", "b"),
        value_match=_match(VALUE_COLUMNS, "n", "b"),
    )


__all__ = ["KEY_COLUMNS", "VALUE_COLUMNS", "redundant_ids_sql"]
