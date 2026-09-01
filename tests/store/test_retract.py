"""Retracting a value that cannot be attributed to anything.

Not deleting it. `sec-nport` once keyed OTC derivatives as
`otc:<counterparty>:<kind>:<date>`, putting every contract a fund held with
one broker on one subject — correct numbers under a key that cannot say
which contract each belongs to. 37 such keys still hold contradictory values
at their newest knowledge time, and nothing can supersede them because the
current parser writes to different subjects entirely.

The test that matters is `TestNothingIsDeleted`: the point of writing a null
rather than removing a row is that the record of what was believed survives.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.store.retract import (
    RETRACTION_SOURCE,
    Unattributable,
    retract_all,
    retraction,
)

SUBJECT = TUID("otc:BANK_OF_AMERICA,_N.A.:futrDeriv:open")
FIELD = "nport:valUSD"
EFF = date(2026, 4, 30)
T0 = datetime(2026, 7, 26, 18, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
REASON = "subject scheme superseded: this key conflated several contracts"


def _inputs(store: DuckStore, values: list[float]) -> tuple[str, ...]:
    """Contradictory rows on one key at one knowledge time, as nport left them."""
    ids: list[str] = []
    for index, value in enumerate(values):
        record = Provenance(
            source_system="sec-nport",
            source_uri=f"https://www.sec.gov/Archives/edgar/data/1/{index}/primary_doc.xml",
            retrieved_at=T0,
            method=ExtractionMethod.XBRL,
            extractor_version="1",
        )
        store.write_provenance([record])
        store.write_facts(
            [
                Fact(
                    subject=SUBJECT,
                    field=FIELD,
                    value=value,
                    effective_from=EFF,
                    knowledge_from=T0,
                    provenance_id=record.id,
                )
            ]
        )
        ids.append(record.id)
    return tuple(ids)


def _item(inputs: tuple[str, ...]) -> Unattributable:
    return Unattributable(
        subject=SUBJECT, field=FIELD, effective_from=EFF, effective_to=None, inputs=inputs
    )


class TestTheRetractionItself:
    def test_it_writes_a_null_not_a_number(self) -> None:
        _, fact = retraction(_item(("a",)), reason=REASON, at=LATER)
        assert fact.value is None

    def test_it_lands_at_a_new_knowledge_time(self) -> None:
        """Latest-knowledge-wins is what makes it take effect."""
        _, fact = retraction(_item(("a",)), reason=REASON, at=LATER)
        assert fact.knowledge_from == LATER

    def test_the_provenance_names_its_inputs(self) -> None:
        """I1: a derived value carries provenance referencing its inputs.
        `SPTR` on a retracted key must walk back to the rows that could not
        be told apart."""
        record, _ = retraction(_item(("b", "a")), reason=REASON, at=LATER)
        assert record.method is ExtractionMethod.DERIVED
        assert record.input_ids == ("a", "b")

    def test_the_reason_is_carried_where_a_person_will_see_it(self) -> None:
        record, _ = retraction(_item(("a",)), reason=REASON, at=LATER)
        assert record.source_uri == REASON

    def test_it_is_not_filed_under_the_source_s_name(self) -> None:
        """The SEC did not say this; we did, about what the SEC gave us.
        Filing it as `sec-nport` would also make the health report count it
        as that source flowing."""
        record, _ = retraction(_item(("a",)), reason=REASON, at=LATER)
        assert record.source_system == RETRACTION_SOURCE

    def test_a_retraction_with_no_inputs_is_refused(self) -> None:
        """It would assert that nothing is known here without saying what
        it supersedes — an unexplained hole rather than a correction."""
        with pytest.raises(ValueError, match="no inputs"):
            retraction(_item(()), reason=REASON, at=LATER)

    def test_a_batch_shares_one_instant(self) -> None:
        """One act of correction. Spreading it across microseconds would
        make the write order look like information."""
        items = [_item(("a",)), _item(("b",))]
        _, facts = retract_all(items, reason=REASON)
        assert len({f.knowledge_from for f in facts}) == 1


class TestAgainstAStore:
    def test_the_key_resolves_to_no_value(self, tmp_path: Path) -> None:
        store = DuckStore(tmp_path / "s.db")
        inputs = _inputs(store, [-28204.21, -11818.67])
        records, facts = retract_all([_item(inputs)], reason=REASON, at=LATER)
        store.write_provenance(records)
        store.write_facts(facts)

        (visible,) = store.history(SUBJECT, FIELD, as_of=datetime(2026, 10, 1, tzinfo=UTC))
        assert visible.value is None

    def test_the_contradiction_no_longer_stands_at_the_newest_time(self, tmp_path: Path) -> None:
        store = DuckStore(tmp_path / "s.db")
        inputs = _inputs(store, [-28204.21, -11818.67])
        records, facts = retract_all([_item(inputs)], reason=REASON, at=LATER)
        store.write_provenance(records)
        store.write_facts(facts)
        rows = store._conn.execute(
            "SELECT count(DISTINCT coalesce(value_num, -1e308)) FROM facts "
            "WHERE subject = ? AND field = ? AND knowledge_from = ?",
            [SUBJECT, FIELD, LATER],
        ).fetchone()
        assert rows is not None and rows[0] == 1

    def test_before_retraction_the_key_is_ambiguous(self, tmp_path: Path) -> None:
        """Proves the two assertions above turn on the retraction rather
        than on the fixture being unambiguous to begin with."""
        store = DuckStore(tmp_path / "s.db")
        _inputs(store, [-28204.21, -11818.67])
        rows = store._conn.execute(
            "SELECT count(DISTINCT value_num) FROM facts WHERE subject = ? AND field = ?",
            [SUBJECT, FIELD],
        ).fetchone()
        assert rows is not None and rows[0] == 2


class TestNothingIsDeleted:
    """The reason a null is written rather than a row removed.

    I2 is inserts only, and those rows are a true record of what was
    believed. A correction that erased them would make the store unable to
    answer what it thought last month, which is the property the whole
    bitemporal design exists to provide.
    """

    def test_the_original_rows_survive(self, tmp_path: Path) -> None:
        store = DuckStore(tmp_path / "s.db")
        inputs = _inputs(store, [-28204.21, -11818.67])
        records, facts = retract_all([_item(inputs)], reason=REASON, at=LATER)
        store.write_provenance(records)
        store.write_facts(facts)
        assert store.fact_count() == 3

    def test_the_earlier_world_still_answers_as_it_did(self, tmp_path: Path) -> None:
        """An `as_of` before the retraction sees what was believed then —
        ambiguity and all."""
        store = DuckStore(tmp_path / "s.db")
        inputs = _inputs(store, [-28204.21, -11818.67])
        records, facts = retract_all([_item(inputs)], reason=REASON, at=LATER)
        store.write_provenance(records)
        store.write_facts(facts)

        (before,) = store.history(SUBJECT, FIELD, as_of=datetime(2026, 8, 1, tzinfo=UTC))
        assert before.value is not None
