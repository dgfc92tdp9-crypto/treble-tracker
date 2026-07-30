"""TQL parsing (spec §4.2).

The two queries the specification actually writes down are the contract;
everything else here defends the properties that make a parsed query safe
to execute.
"""

from __future__ import annotations

from datetime import date

import pytest

from treble.tql.grammar import Comparison, Tenor, TqlSyntaxError, parse_tql

SPEC_BONDS = """
get(
  px_last,
  oas_spread_mid(vol_override=0.20),
  dur_adj_oas
)
for(
  bonds(issuer_ticker='IBM', currency='USD', amt_outstanding > 250e6)
)
with(dates=range(-1Y, 0D), fill='prev')
"""
SPEC_MEMBERS = "get(px_last) for(members('SPX Index')) with(dates=range(-1Y,0D))"


class TestTheSpecExamples:
    def test_the_section_4_2_query_parses(self) -> None:
        query = parse_tql(SPEC_BONDS)
        assert [f.mnemonic for f in query.fields] == [
            "PX_LAST",
            "OAS_SPREAD_MID",
            "DUR_ADJ_OAS",
        ]
        assert query.selector.name == "bonds"
        assert len(query.selector.predicates) == 3

    def test_overrides_travel_with_their_field(self) -> None:
        """§4.2 calls overrides 'the mechanism by which the entire analytics
        library is exposed as data'. A dropped override silently prices under
        a different assumption than the one asked for."""
        query = parse_tql(SPEC_BONDS)
        oas = next(f for f in query.fields if f.mnemonic == "OAS_SPREAD_MID")
        assert oas.overrides == (("vol_override", 0.20),)
        assert next(f for f in query.fields if f.mnemonic == "PX_LAST").overrides == ()

    def test_the_spreadsheet_example_parses(self) -> None:
        query = parse_tql(SPEC_MEMBERS)
        assert query.selector.name == "members"
        assert query.selector.arguments == ("SPX Index",)

    def test_predicate_comparisons_survive(self) -> None:
        """`amt_outstanding > 250e6` is a filter. Reading it as equality
        would return a universe of bonds with exactly that size."""
        amount = parse_tql(SPEC_BONDS).selector.predicates[2]
        assert amount.comparison is Comparison.GT
        assert amount.value == 250_000_000


class TestTenorsResolveAgainstAsOf:
    def test_a_tenor_is_not_resolved_at_parse_time(self) -> None:
        """I2: the same query must mean the same thing whenever it is
        parsed. Resolving -1Y against the parse clock would make a saved
        query drift silently."""
        assert parse_tql(SPEC_BONDS).dates.start == Tenor(amount=-1, unit="Y")

    @pytest.mark.parametrize(
        ("tenor", "expected"),
        [
            ("-1Y", date(2025, 7, 30)),
            ("0D", date(2026, 7, 30)),
            ("-30D", date(2026, 6, 30)),
            ("-2W", date(2026, 7, 16)),
            ("-3M", date(2026, 4, 30)),
        ],
    )
    def test_resolution(self, tenor: str, expected: date) -> None:
        assert Tenor.parse(tenor).resolve(date(2026, 7, 30)) == expected

    def test_month_arithmetic_clamps_to_a_real_day(self) -> None:
        """31 March less one month is 28 February, not the 31st."""
        assert Tenor.parse("-1M").resolve(date(2026, 3, 31)) == date(2026, 2, 28)

    def test_a_leap_day_survives_a_year_step(self) -> None:
        assert Tenor.parse("-1Y").resolve(date(2028, 2, 29)) == date(2027, 2, 28)


class TestParsingIsTotal:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "get(px_last)",  # no universe
            "for(bonds())",  # nothing retrieved
            "get(px_last) for(",
            "get(px_last) for(bonds()) with(dates=range(-1Y))",
            "nonsense",
        ],
    )
    def test_malformed_queries_raise(self, text: str) -> None:
        """Never a partial tree: a half-parsed query would run against a
        universe nobody asked for."""
        with pytest.raises(TqlSyntaxError):
            parse_tql(text)

    def test_a_range_of_non_tenors_is_rejected(self) -> None:
        with pytest.raises(TqlSyntaxError, match="two tenors"):
            parse_tql("get(px_last) for(bonds()) with(dates=range(1, 2))")

    def test_clause_keywords_are_case_insensitive(self) -> None:
        assert parse_tql("GET(px_last) FOR(bonds())").fields[0].mnemonic == "PX_LAST"

    def test_a_universe_with_no_predicates_is_valid(self) -> None:
        assert parse_tql("get(px_last) for(bonds())").selector.predicates == ()

    def test_options_are_optional(self) -> None:
        assert parse_tql("get(px_last) for(bonds())").options == ()


class TestQueryIsImmutable:
    def test_a_parsed_query_cannot_be_edited(self) -> None:
        """A query is evidence of what was asked; mutating one after the
        fact would detach the result from the request."""
        query = parse_tql(SPEC_BONDS)
        with pytest.raises(Exception, match=r"frozen|immutable"):
            query.fields = ()  # type: ignore[misc]
