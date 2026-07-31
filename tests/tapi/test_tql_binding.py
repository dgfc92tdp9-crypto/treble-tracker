"""TQL-backed panes (spec §7.7, §14.3).

`SRCH` and `EQS` are screens whose pane is a query. This is the only path
from a screen to TQL, and it runs through TAPI — which is what keeps I7
intact while `tql` sits below `tapi` in the layering.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.local import TQL_BINDING, LocalTapi

AS_OF = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def tapi(tmp_path: Path) -> LocalTapi:
    store = DuckStore(tmp_path / "q.db")
    record = Provenance(
        source_system="treasury",
        source_uri="https://example.test/auctions",
        retrieved_at=AS_OF,
        method=ExtractionMethod.API,
        extractor_version="1",
    )
    store.write_provenance([record])
    store.write_facts(
        [
            Fact(
                subject=f"cusip:{cusip}",
                field=field,
                value=value,  # type: ignore[arg-type]
                effective_from=date(2026, 1, 1),
                knowledge_from=datetime(2026, 1, 2, tzinfo=UTC),
                provenance_id=record.id,
            )
            for cusip, field, value in [
                ("AAA", "security_type", "Bond"),
                ("AAA", "int_rate", 4.625),
                ("BBB", "security_type", "Note"),
                ("BBB", "int_rate", 3.5),
            ]
        ]
    )
    return LocalTapi(store)


def _rows(tapi: LocalTapi, query: str) -> tuple:
    return tapi.series(None, f"{TQL_BINDING}{query}", as_of=AS_OF)


class TestQueryPanes:
    def test_a_query_pane_returns_matching_rows(self, tapi: LocalTapi) -> None:
        rows = _rows(tapi, "get(int_rate) for(bonds(security_type='Bond'))")
        assert rows == (("AAA", 4.625),)

    def test_the_subject_namespace_is_stripped_for_display(self, tapi: LocalTapi) -> None:
        """A screen shows a CUSIP, not `cusip:AAA`."""
        assert _rows(tapi, "get(int_rate) for(bonds())")[0][0] == "AAA"

    def test_a_field_with_no_value_is_blank_not_missing(self, tapi: LocalTapi) -> None:
        rows = _rows(tapi, "get(int_rate, nonexistent) for(bonds(security_type='Bond'))")
        assert rows[0][2] is None


class TestFailuresAreVisible:
    def test_a_syntax_error_surfaces_as_a_row(self, tapi: LocalTapi) -> None:
        """An empty table would report 'no matches' for a broken query.
        SRCH finding nothing must be distinguishable from SRCH failing."""
        rows = _rows(tapi, "get(int_rate) for(")
        assert rows and "query failed" in str(rows[0][0])

    def test_an_unselectable_universe_surfaces_too(self, tapi: LocalTapi) -> None:
        rows = _rows(tapi, "get(int_rate) for(galaxies())")
        assert rows and "query failed" in str(rows[0][0])

    def test_a_working_query_never_reports_failure(self, tapi: LocalTapi) -> None:
        rows = _rows(tapi, "get(int_rate) for(bonds())")
        assert not any("query failed" in str(row[0]) for row in rows)


class TestShippedScreenQueries:
    @pytest.mark.parametrize("mnemonic", ["SRCH", "EQS"])
    def test_the_screens_queries_parse(self, mnemonic: str) -> None:
        """A query written into a definition is data, not code — so nothing
        would catch a malformed one at import. This does."""
        from treble.render.contract.registry import get_screen
        from treble.render.contract.schema import PaneRegion
        from treble.tql.grammar import parse_tql

        panes = [
            cell
            for cell in get_screen(mnemonic).tabs[0].cells
            if isinstance(cell, PaneRegion) and cell.binding.startswith(TQL_BINDING)
        ]
        assert panes, f"{mnemonic} has no TQL pane"
        for pane in panes:
            parse_tql(pane.binding.removeprefix(TQL_BINDING))
