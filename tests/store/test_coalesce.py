"""A row is not written to say a value has not changed.

The test that carries this file is
:class:`TestEveryAsOfAnswerIsUnchanged`. Everything above it checks a rule
in isolation, and a rule checked in isolation is exactly how you ship a
filter that drops the wrong row — so the property test rebuilds the same
history twice, once through `write_facts` and once through a deliberately
stupid reference insert that coalesces nothing, and asserts the two stores
answer every `as_of` identically.

The reference implementation is raw SQL on purpose. Comparing the filter
against itself would prove only that it is consistent, which is the failure
this repository has caught four times.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore, _decompose

SUBJECT = TUID("fred:BAMLC0A0CM")
FIELD = "PX_LAST"
EFF = date(2025, 9, 3)


def _provenance(source: str, when: datetime) -> Provenance:
    return Provenance(
        source_system=source,
        source_uri=f"https://example.invalid/{source}/{when.isoformat()}",
        retrieved_at=when,
        method=ExtractionMethod.API,
        extractor_version="1",
        confidence=1.0,
    )


def _observe(
    store: DuckStore,
    value: object,
    when: datetime,
    *,
    source: str = "fred",
    subject: TUID = SUBJECT,
    effective_from: date = EFF,
    effective_to: date | None = None,
) -> None:
    """Record one observation, provenance first."""
    record = _provenance(source, when)
    store.write_provenance([record])
    store.write_facts(
        [
            Fact(
                subject=subject,
                field=FIELD,
                value=value,  # type: ignore[arg-type]
                effective_from=effective_from,
                effective_to=effective_to,
                knowledge_from=when,
                provenance_id=record.id,
            )
        ]
    )


T0 = datetime(2026, 7, 27, 16, 0, tzinfo=UTC)


def _at(days: int) -> datetime:
    return T0 + timedelta(days=days)


class TestARepeatedObservationIsNotStored:
    def test_the_same_value_from_the_same_source_is_written_once(self, tmp_path: Path) -> None:
        """The live case: `0.81` present eight times, once per refresh,
        never having changed."""
        store = DuckStore(tmp_path / "s.db")
        for day in range(8):
            _observe(store, 0.81, _at(day))
        assert store.fact_count() == 1
        assert store.coalesced == 7

    def test_the_stored_row_is_the_first_observation(self, tmp_path: Path) -> None:
        """`core.facts` defines `knowledge_from` as when the system could
        **first** have known it. Plain append made the visible row carry the
        *latest* re-fetch instead, for 32% of the live store."""
        store = DuckStore(tmp_path / "s.db")
        for day in range(3):
            _observe(store, 0.81, _at(day))
        (fact,) = store.history(SUBJECT, FIELD, as_of=_at(10))
        assert fact.knowledge_from == T0

    def test_the_count_is_reported(self, tmp_path: Path) -> None:
        """A filter nobody can see is indistinguishable from one that
        silently drops real data."""
        store = DuckStore(tmp_path / "s.db")
        assert store.coalesced == 0
        _observe(store, 0.81, _at(0))
        assert store.coalesced == 0, "the first observation is not redundant"
        _observe(store, 0.81, _at(1))
        assert store.coalesced == 1


class TestRealChangesAreAlwaysWritten:
    """Each of these is a way the filter could be wrong that costs data."""

    def test_a_restatement_is_written(self, tmp_path: Path) -> None:
        store = DuckStore(tmp_path / "s.db")
        _observe(store, 0.81, _at(0))
        _observe(store, 0.85, _at(1))
        assert store.fact_count() == 2

    def test_a_value_returning_to_an_earlier_one_is_written(self, tmp_path: Path) -> None:
        """A → B → A. Comparing against *any* prior row rather than the
        newest one would drop the third observation and leave the store
        saying the value is still B."""
        store = DuckStore(tmp_path / "s.db")
        _observe(store, 0.81, _at(0))
        _observe(store, 0.85, _at(1))
        _observe(store, 0.81, _at(2))
        assert store.fact_count() == 3
        (fact,) = store.history(SUBJECT, FIELD, as_of=_at(9))
        assert fact.value == 0.81

    def test_a_second_source_is_always_written(self, tmp_path: Path) -> None:
        """Reads do not partition by source, so dropping this would change
        which source the visible value traces to — an I1 provenance change
        wearing a space saving as a disguise. Corroboration is information."""
        store = DuckStore(tmp_path / "s.db")
        _observe(store, 0.81, _at(0), source="fred")
        _observe(store, 0.81, _at(1), source="ecb")
        assert store.fact_count() == 2

    def test_older_knowledge_arriving_late_is_written(self, tmp_path: Path) -> None:
        """A backfill of knowledge older than the newest row is judged
        against what was visible *then*, not now. Rather than carry a rule
        with a case nobody exercises, those are written unconditionally."""
        store = DuckStore(tmp_path / "s.db")
        _observe(store, 0.81, _at(5))
        _observe(store, 0.81, _at(1))
        assert store.fact_count() == 2

    def test_a_different_effective_date_is_a_different_assertion(self, tmp_path: Path) -> None:
        store = DuckStore(tmp_path / "s.db")
        _observe(store, 0.81, _at(0), effective_from=date(2025, 9, 3))
        _observe(store, 0.81, _at(0), effective_from=date(2025, 9, 4))
        assert store.fact_count() == 2

    def test_a_different_effective_end_is_a_different_assertion(self, tmp_path: Path) -> None:
        """The same value over a *different period* is a different claim.
        Two facts sharing `effective_from` and differing only in
        `effective_to` — an open-ended one and one that closes — are both
        real, and a key that stopped at `effective_from` would keep the
        first and silently discard the second.

        Added because dropping `effective_to` from the key survived
        mutation: the case existed only for `effective_from`, so the half
        of the key that handles closed periods was never exercised.
        """
        store = DuckStore(tmp_path / "s.db")
        _observe(store, 0.81, _at(0), effective_to=None)
        _observe(store, 0.81, _at(1), effective_to=date(2025, 12, 31))
        assert store.fact_count() == 2
        _observe(store, 0.81, _at(2), effective_to=date(2025, 12, 31))
        assert store.fact_count() == 2, "the closed period repeated is still one claim"

    def test_a_different_subject_is_a_different_assertion(self, tmp_path: Path) -> None:
        store = DuckStore(tmp_path / "s.db")
        _observe(store, 0.81, _at(0), subject=TUID("fred:DGS10"))
        _observe(store, 0.81, _at(1), subject=TUID("fred:DGS2"))
        assert store.fact_count() == 2

    def test_a_null_does_not_coalesce_against_a_stated_value(self, tmp_path: Path) -> None:
        """Every typed column is null in both rows, so a comparison over
        value columns alone would call these the same assertion. `A value
        is missing` and `the value is 0.81` are different claims —
        `value_kind` is what keeps them apart."""
        store = DuckStore(tmp_path / "s.db")
        _observe(store, 0.81, _at(0))
        _observe(store, None, _at(1))
        assert store.fact_count() == 2
        _observe(store, None, _at(2))
        assert store.fact_count() == 2, "two nulls in a row are still one claim"


class TestValueKindIsImpliedToday:
    """Why `value_kind` in the comparison currently cannot change anything.

    Mutation testing removed it and killed no test. That is not a missing
    test — it is a true statement about `_decompose`, which gives every kind
    but `null` exactly one non-null typed column. The comparison over typed
    columns therefore already separates every value a `Fact` can carry.

    So this pins the *reason* rather than inventing a case. A kind added
    later that leaves every typed column null — a redacted or
    restriction-withheld value, which this domain will want — would
    coalesce into a plain null and lose the distinction. On that day this
    fails, and `VALUE_COLUMNS` stops being belt-and-braces.
    """

    def test_only_null_leaves_every_typed_column_empty(self) -> None:
        kinds = [_decompose(value) for value in (None, True, 7, 1.5, "text", date(2025, 1, 1))]
        empty = [kind for kind, *columns in kinds if all(c is None for c in columns)]
        assert empty == ["null"], (
            "a value kind now carries its meaning in the kind alone; "
            "value_kind in VALUE_COLUMNS is load-bearing and needs its own test"
        )

    def test_every_kind_is_distinguishable_by_its_columns(self) -> None:
        decomposed = [_decompose(value) for value in (True, 7, 1.5, "text", date(2025, 1, 1))]
        assert len({tuple(columns) for _, *columns in decomposed}) == len(decomposed)


class TestAcrossTheColdTier:
    def test_a_value_settled_into_parquet_still_coalesces(self, tmp_path: Path) -> None:
        """The saving would evaporate on any store old enough to have been
        compacted, which is every store that has run for a while — and the
        comparison would silently start passing everything through with no
        test to say so."""
        store = DuckStore(tmp_path / "s.db")
        _observe(store, 0.81, _at(0))
        store.compact(before=_at(1))
        assert store.hot_fact_count() == 0, "nothing moved to the cold tier"
        _observe(store, 0.81, _at(1))
        assert store.hot_fact_count() == 0, "the cold tier was not consulted"
        assert store.coalesced == 1


class TestEveryAsOfAnswerIsUnchanged:
    """The property the whole change rests on.

    A history is written twice: once through `write_facts`, once through a
    reference insert that coalesces nothing. Every `as_of` across the whole
    knowledge range must yield the same values from both.
    """

    #: value, day observed. Repeats, restatements, a return to an earlier
    #: value, a null, and a second source — every branch above, interleaved.
    HISTORY: tuple[tuple[float | None, int, str], ...] = (
        (0.81, 0, "fred"),
        (0.81, 1, "fred"),
        (0.81, 2, "fred"),
        (0.85, 3, "fred"),
        (0.85, 4, "fred"),
        (0.81, 5, "fred"),
        (None, 6, "fred"),
        (None, 7, "fred"),
        (0.90, 8, "ecb"),
        (0.90, 9, "ecb"),
        (0.81, 10, "fred"),
    )

    def _reference(self, path: Path) -> DuckStore:
        """The same history with no filter at all, inserted underneath the
        write path so nothing about this file can make both agree by
        agreeing with each other."""
        store = DuckStore(path)
        for value, day, source in self.HISTORY:
            record = _provenance(source, _at(day))
            store.write_provenance([record])
            # Deliberately below the filter: this is the reference.
            store._conn.execute(
                "INSERT INTO facts (subject, field, value_kind, value_num, value_int, "
                "value_text, value_bool, value_date, effective_from, effective_to, "
                "knowledge_from, provenance_id) VALUES (?, ?, ?, ?, NULL, NULL, NULL, "
                "NULL, ?, NULL, ?, ?)",
                [
                    SUBJECT,
                    FIELD,
                    "null" if value is None else "num",
                    value,
                    EFF,
                    _at(day),
                    record.id,
                ],
            )
        return store

    def _coalesced(self, path: Path) -> DuckStore:
        store = DuckStore(path)
        for value, day, source in self.HISTORY:
            _observe(store, value, _at(day), source=source)
        return store

    @pytest.mark.parametrize("day", range(12))
    def test_the_same_value_is_visible_on_every_day(self, tmp_path: Path, day: int) -> None:
        reference = self._reference(tmp_path / "ref.db")
        coalesced = self._coalesced(tmp_path / "coa.db")
        as_of = _at(day) + timedelta(hours=1)
        expected = [f.value for f in reference.history(SUBJECT, FIELD, as_of=as_of)]
        actual = [f.value for f in coalesced.history(SUBJECT, FIELD, as_of=as_of)]
        assert actual == expected, f"as_of day {day} disagrees"

    def test_the_reference_really_is_uncoalesced(self, tmp_path: Path) -> None:
        """Proves the comparison above can fail. If the reference were
        quietly going through the same filter, both stores would hold the
        same rows and every assertion in this class would pass for a reason
        that has nothing to do with the property being tested."""
        reference = self._reference(tmp_path / "ref.db")
        coalesced = self._coalesced(tmp_path / "coa.db")
        assert reference.fact_count() == len(self.HISTORY)
        assert coalesced.fact_count() < reference.fact_count()

    def test_the_saving_is_real(self, tmp_path: Path) -> None:
        """Five, counted by hand off `HISTORY`: days 1 and 2 repeating
        day 0, day 4 repeating day 3, day 7 repeating the null on day 6,
        and day 9 repeating the ECB observation on day 8. Day 5 (a return
        to 0.81 after 0.85) and day 10 (0.81 after a null) are changes."""
        coalesced = self._coalesced(tmp_path / "coa.db")
        assert coalesced.coalesced == 5


class TestTheCountReconcilesWithTheStore:
    """`treble refresh` reports `parsed - coalesced` as what it stored.

    That arithmetic is only right if the counter and the row count move
    together on every write. If they ever drift the command reports a
    number nobody can reconcile against the store — worse than the parsed
    figure it replaced, because it looks reconciled.
    """

    def _write(self, store: DuckStore, values: list[float | None], day: int) -> tuple[int, int]:
        """One batch, returning (rows added, rows coalesced)."""
        record = _provenance("fred", _at(day))
        store.write_provenance([record])
        facts = [
            Fact(
                subject=SUBJECT,
                field=FIELD,
                value=value,  # type: ignore[arg-type]
                effective_from=date(2025, 9, 3) + timedelta(days=i),
                knowledge_from=_at(day),
                provenance_id=record.id,
            )
            for i, value in enumerate(values)
        ]
        rows, coalesced = store.fact_count(), store.coalesced
        store.write_facts(facts)
        return store.fact_count() - rows, store.coalesced - coalesced

    def test_added_plus_coalesced_is_the_batch(self, tmp_path: Path) -> None:
        store = DuckStore(tmp_path / "s.db")
        batch: list[float | None] = [1.0, 2.0, 3.0, None]

        added, coalesced = self._write(store, batch, 0)
        assert (added, coalesced) == (len(batch), 0), "a first write coalesces nothing"

        added, coalesced = self._write(store, batch, 1)
        assert added + coalesced == len(batch)
        assert added == 0, "nothing changed, so nothing should have been stored"

    def test_a_partly_changed_batch_splits_correctly(self, tmp_path: Path) -> None:
        """The case the report exists for: some of it is news."""
        store = DuckStore(tmp_path / "s.db")
        self._write(store, [1.0, 2.0, 3.0], 0)
        added, coalesced = self._write(store, [1.0, 99.0, 3.0], 1)
        assert (added, coalesced) == (1, 2)

    def test_an_empty_batch_moves_neither(self, tmp_path: Path) -> None:
        store = DuckStore(tmp_path / "s.db")
        assert self._write(store, [], 0) == (0, 0)
