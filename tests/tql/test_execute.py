"""TQL planning and execution (spec §4.2)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.tql.execute import TqlExecutionError, execute, plan
from treble.tql.grammar import parse_tql

AS_OF = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


class FakeSource:
    """Recorded facts, so a plan can be executed without a database."""

    def __init__(self, facts: list[Fact]) -> None:
        self._facts = facts

    def read(self, subject: TUID, field: str, *, as_of: datetime) -> list[Fact]:
        return [
            f
            for f in self._facts
            if f.subject == subject and f.field == field and f.knowledge_from <= as_of
        ]

    def subjects_with_prefix(self, prefix: str, *, as_of: datetime) -> list[TUID]:
        return sorted(
            {
                f.subject
                for f in self._facts
                if f.subject.startswith(prefix) and f.knowledge_from <= as_of
            }
        )


def _fact(subject: str, field: str, value: object) -> Fact:
    return Fact(
        subject=subject,
        field=field,
        value=value,  # type: ignore[arg-type]
        effective_from=date(2026, 1, 1),
        knowledge_from=datetime(2026, 1, 2, tzinfo=UTC),
        provenance_id="p" * 12,
    )


@pytest.fixture
def source() -> FakeSource:
    return FakeSource(
        [
            _fact("cusip:AAA", "int_rate", 4.625),
            _fact("cusip:AAA", "security_type", "Bond"),
            _fact("cusip:BBB", "int_rate", 3.5),
            _fact("cusip:BBB", "security_type", "Bond"),
            _fact("cusip:CCC", "int_rate", 5.0),
            _fact("cusip:CCC", "security_type", "Note"),
            _fact("cik:0000051143", "int_rate", 9.9),  # a different universe
        ]
    )


class TestPlanning:
    def test_relative_dates_resolve_once(self) -> None:
        """Every field in a query must see the same window; each resolving
        its own clock would let one column drift from the next."""
        query = parse_tql("get(px_last) for(bonds()) with(dates=range(-1Y, 0D))")
        assert plan(query, as_of=AS_OF).window == (date(2025, 7, 30), date(2026, 7, 30))

    def test_a_plan_can_be_explained_before_it_runs(self) -> None:
        query = parse_tql("get(int_rate) for(bonds(security_type='Bond'))")
        assert "int_rate" in plan(query, as_of=AS_OF).explain()

    def test_an_unknown_universe_raises(self) -> None:
        """Not an empty result: that is indistinguishable from a universe
        that is genuinely empty."""
        query = parse_tql("get(px_last) for(galaxies())")
        with pytest.raises(TqlExecutionError, match="not selectable yet"):
            plan(query, as_of=AS_OF)

    def test_a_named_universe_says_what_is_missing(self) -> None:
        query = parse_tql("get(px_last) for(members('SPX Index'))")
        with pytest.raises(TqlExecutionError, match="membership"):
            plan(query, as_of=AS_OF)


class TestExecution:
    def _run(self, text: str, source: FakeSource):  # type: ignore[no-untyped-def]
        return execute(plan(parse_tql(text), as_of=AS_OF), source)

    def test_predicates_narrow_the_universe(self, source: FakeSource) -> None:
        result = self._run("get(int_rate) for(bonds(security_type='Bond'))", source)
        assert [r.subject for r in result.rows] == ["cusip:AAA", "cusip:BBB"]

    def test_comparisons_filter(self, source: FakeSource) -> None:
        result = self._run("get(int_rate) for(bonds(int_rate > 4.0))", source)
        assert [r.subject for r in result.rows] == ["cusip:AAA", "cusip:CCC"]

    def test_predicates_combine(self, source: FakeSource) -> None:
        result = self._run("get(int_rate) for(bonds(security_type='Bond', int_rate > 4.0))", source)
        assert [r.subject for r in result.rows] == ["cusip:AAA"]

    def test_the_universe_bounds_the_result(self, source: FakeSource) -> None:
        """The cik: subject has an int_rate too, and must not appear in a
        query for bonds."""
        result = self._run("get(int_rate) for(bonds())", source)
        assert all(r.subject.startswith("cusip:") for r in result.rows)

    def test_a_missing_attribute_excludes_rather_than_widens(self) -> None:
        """A subject with no value for a filtered attribute is out. Letting
        it through would make a predicate widen the universe."""
        source = FakeSource([_fact("cusip:AAA", "int_rate", 4.0)])
        result = self._run("get(int_rate) for(bonds(security_type='Bond'))", source)
        assert result.rows == ()

    def test_every_value_carries_provenance(self, source: FakeSource) -> None:
        """I1 holds for query results as for screens."""
        result = self._run("get(int_rate) for(bonds(int_rate > 4.0))", source)
        assert all(p[1] for row in result.rows for p in row.provenance)

    def test_a_field_with_no_value_is_null_not_absent(self, source: FakeSource) -> None:
        """The column exists and is empty, so a caller can tell 'not
        reported' from 'not requested'."""
        result = self._run("get(int_rate, nonexistent) for(bonds(int_rate > 4.5))", source)
        assert dict(result.rows[0].values)["nonexistent"] is None

    def test_results_are_ordered(self, source: FakeSource) -> None:
        """Two runs of one query must not look like different answers."""
        first = self._run("get(int_rate) for(bonds())", source)
        second = self._run("get(int_rate) for(bonds())", source)
        assert [r.subject for r in first.rows] == [r.subject for r in second.rows]

    def test_point_in_time_is_honoured(self, source: FakeSource) -> None:
        """I2: nothing known after as_of is visible."""
        early = execute(
            plan(parse_tql("get(int_rate) for(bonds())"), as_of=datetime(2026, 1, 1, tzinfo=UTC)),
            source,
        )
        assert early.rows == ()
