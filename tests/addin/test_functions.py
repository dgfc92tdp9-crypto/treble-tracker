"""Spreadsheet functions (spec §4.1, §4.2).

Tested as plain functions over a fake TAPI, because a spreadsheet function
that can only be exercised inside Excel is one that does not get exercised.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from treble.addin.functions import BLANK, parse_overrides, tdh, tdp, tds
from treble.core.identifiers import SecurityQuery
from treble.tapi.types import FieldResult

AS_OF = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


class FakeTapi:
    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, str]]] = []

    def field(
        self,
        security: SecurityQuery | None,
        mnemonic: str,
        overrides: dict[str, str],
        *,
        as_of: datetime,
    ) -> FieldResult:
        self.seen.append((mnemonic, overrides))
        if mnemonic == "MISSING":
            return FieldResult(value=None)
        if mnemonic == "BOOM":
            raise KeyError("no such field")
        return FieldResult(value=42.0 * (1 + len(overrides)))

    def series(
        self, security: SecurityQuery | None, binding: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        if binding == "EMPTY":
            return ()
        if binding == "BOOM":
            raise KeyError("no such series")
        return (
            ("2026-07-20", 1.0),
            ("2026-07-21", 2.0),
            ("2026-07-22", 3.0),
        )


@pytest.fixture
def tapi() -> FakeTapi:
    return FakeTapi()


class TestOverrides:
    def test_key_value_pairs_are_parsed(self) -> None:
        assert parse_overrides(("Per=D", "Fill=P")) == {"Per": "D", "Fill": "P"}

    def test_an_argument_without_an_equals_is_ignored(self) -> None:
        """Guessing a key would run the model under an assumption nobody
        wrote."""
        assert parse_overrides(("nonsense",)) == {}

    def test_whitespace_is_trimmed(self) -> None:
        assert parse_overrides((" Per = D ",)) == {"Per": "D"}

    def test_overrides_reach_tapi(self, tapi: FakeTapi) -> None:
        tdp(tapi, "IBM US Equity", "OAS_SPREAD_MID", "vol_override=0.20", as_of=AS_OF)
        assert tapi.seen[-1][1] == {"vol_override": "0.20"}


class TestTdp:
    def test_a_value_comes_back(self, tapi: FakeTapi) -> None:
        assert tdp(tapi, "IBM US Equity", "PX_LAST", as_of=AS_OF) == 42.0

    def test_an_absent_value_is_the_same_dash_the_screens_use(self, tapi: FakeTapi) -> None:
        assert tdp(tapi, "IBM US Equity", "MISSING", as_of=AS_OF) == BLANK

    def test_a_failure_explains_itself_in_the_cell(self, tapi: FakeTapi) -> None:
        """A raising formula shows #VALUE! and says nothing, so a user
        cannot tell a wrong ticker from a broken install."""
        assert str(tdp(tapi, "IBM US Equity", "BOOM", as_of=AS_OF)).startswith("#TREBLE")

    def test_an_unparseable_security_explains_itself(self, tapi: FakeTapi) -> None:
        assert str(tdp(tapi, "", "PX_LAST", as_of=AS_OF)).startswith("#TREBLE")


class TestTdh:
    def test_history_spills_as_date_and_value(self, tapi: FakeTapi) -> None:
        grid = tdh(tapi, "SP500 Index", "PX_LAST", as_of=AS_OF)
        assert grid[0] == ["2026-07-20", 1.0]
        assert len(grid) == 3

    @pytest.mark.parametrize("form", ["2026-07-21", "07/21/2026", "20260721"])
    def test_start_bounds_accept_the_formats_a_spreadsheet_produces(
        self, tapi: FakeTapi, form: str
    ) -> None:
        grid = tdh(tapi, "SP500 Index", "PX_LAST", form, as_of=AS_OF)
        assert [row[0] for row in grid] == ["2026-07-21", "2026-07-22"]

    def test_the_end_bound_is_inclusive(self, tapi: FakeTapi) -> None:
        grid = tdh(tapi, "SP500 Index", "PX_LAST", "", "2026-07-21", as_of=AS_OF)
        assert [row[0] for row in grid] == ["2026-07-20", "2026-07-21"]

    def test_an_empty_history_spills_one_honest_row(self, tapi: FakeTapi) -> None:
        """A formula that spills nothing is indistinguishable from one that
        failed to calculate."""
        assert tdh(tapi, "SP500 Index", "EMPTY", as_of=AS_OF) == [[BLANK, BLANK]]

    def test_a_failure_spills_the_reason(self, tapi: FakeTapi) -> None:
        assert str(tdh(tapi, "SP500 Index", "BOOM", as_of=AS_OF)[0][0]).startswith("#TREBLE")


class TestTds:
    def test_a_data_set_spills_every_column(self, tapi: FakeTapi) -> None:
        assert tds(tapi, "SPX Index", "MEMBERS", as_of=AS_OF)[0] == ["2026-07-20", 1.0]

    def test_an_empty_set_is_a_dash_not_nothing(self, tapi: FakeTapi) -> None:
        assert tds(tapi, "SPX Index", "EMPTY", as_of=AS_OF) == [[BLANK]]
