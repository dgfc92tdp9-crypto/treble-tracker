"""Compliance rules (P3_4).

**A rule that cannot be evaluated must never report a pass.** Most of this
file is that one property, tested from several directions, because it is the
one whose failure is silent: a clean report is what a portfolio manager acts
on and a compliance officer signs, and a rule nobody could test looks
exactly like a rule that passed.

This repository has met the same shape three times before — an analytic with
no data reporting a price, a similarity metric silently dropping two of its
three dimensions, a catalogue claiming "HICP stored" on a store holding
none. Here the cost is a mandate breach nobody was told about.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from treble.compliance.rules import (
    Holding,
    Outcome,
    Predicate,
    Rule,
    RuleSet,
    evaluate,
    run,
)

TODAY = date(2026, 8, 19)


def _holding(identifier: str, value: float, **kwargs: object) -> Holding:
    defaults: dict[str, object] = {
        "issuer": "ACME",
        "maturity": date(2030, 1, 1),
        "currency": "USD",
        "asset_category": "DBT",
    }
    return Holding(identifier, value, **{**defaults, **kwargs})  # type: ignore[arg-type]


def _portfolio() -> tuple[Holding, ...]:
    return (_holding("isin:A", 50.0), _holding("isin:B", 30.0), _holding("isin:C", 20.0))


class TestUnevaluableIsNotAPass:
    """The property the module exists for."""

    def test_a_rating_rule_with_no_ratings_is_not_evaluable(self) -> None:
        rule = Rule("investment grade", Predicate.MIN_RATING, "BBB-")
        result = evaluate(rule, _portfolio(), today=TODAY)
        assert result.outcome is Outcome.NOT_EVALUABLE
        assert result.outcome is not Outcome.PASS

    def test_an_unevaluable_result_is_not_clean(self) -> None:
        """`clean` is what a caller checks, and it must not be satisfied by a
        rule nobody tested."""
        rule = Rule("investment grade", Predicate.MIN_RATING, "BBB-")
        assert evaluate(rule, _portfolio(), today=TODAY).clean is False

    def test_a_report_containing_one_is_not_clean(self) -> None:
        """Even when every other rule passed. A clean report is what somebody
        signs."""
        ruleset = RuleSet(
            "mandate",
            (
                # 100, not 99: the fixture is one issuer at 100%, and the
                # boundary is inclusive — this rule must pass so the report is
                # unclean *only* because of the unevaluable one below.
                Rule("issuer cap", Predicate.MAX_ISSUER_WEIGHT, 100.0),
                Rule("investment grade", Predicate.MIN_RATING, "BBB-"),
            ),
        )
        report = run(ruleset, _portfolio(), today=TODAY)
        assert report.breaches == ()
        assert len(report.not_evaluable) == 1
        assert report.clean is False

    def test_it_names_the_missing_field_and_the_positions(self) -> None:
        """A gap nobody can locate is one nobody can close."""
        rule = Rule("investment grade", Predicate.MIN_RATING, "BBB-")
        result = evaluate(rule, _portfolio(), today=TODAY)
        assert "no rating" in result.detail
        assert "isin:A" in result.offenders

    @pytest.mark.parametrize(
        ("predicate", "limit", "missing"),
        [
            (Predicate.MAX_ISSUER_WEIGHT, 50.0, "issuer"),
            (Predicate.MAX_MATURITY_YEARS, 30.0, "maturity"),
            (Predicate.PERMITTED_CURRENCIES, ("USD",), "currency"),
            (Predicate.PERMITTED_ASSET_CATEGORIES, ("DBT",), "asset_category"),
        ],
    )
    def test_every_predicate_refuses_rather_than_passes_on_missing_data(
        self, predicate: Predicate, limit: object, missing: str
    ) -> None:
        """Parametrised across the whole predicate set on purpose: one branch
        that returned PASS on a gap would be the only one, and would be the
        one nobody looked at."""
        holdings = (_holding("isin:A", 50.0, **{missing: None}), _holding("isin:B", 50.0))
        result = evaluate(Rule("r", predicate, limit), holdings, today=TODAY)  # type: ignore[arg-type]
        assert result.outcome is Outcome.NOT_EVALUABLE

    def test_an_empty_portfolio_proves_nothing(self) -> None:
        """An empty portfolio breaches nothing. Reporting PASS would let a
        fund that had not loaded its holdings look compliant."""
        result = evaluate(Rule("r", Predicate.MAX_ISSUER_WEIGHT, 5.0), (), today=TODAY)
        assert result.outcome is Outcome.NOT_EVALUABLE


class TestTheRulesThemselves:
    def test_issuer_concentration_aggregates_across_positions(self) -> None:
        """Two lines from one issuer are one exposure. Testing positions
        individually would let a 45%/45% pair pass a 50% issuer cap."""
        holdings = (
            _holding("isin:A", 45.0),
            _holding("isin:B", 45.0),
            _holding("isin:C", 10.0, issuer="OTHER"),
        )
        result = evaluate(Rule("cap", Predicate.MAX_ISSUER_WEIGHT, 50.0), holdings, today=TODAY)
        assert result.outcome is Outcome.BREACH
        assert "ACME" in result.offenders[0]

    def test_position_and_issuer_limits_are_different_rules(self) -> None:
        """The same portfolio passes one and breaches the other, which is
        why both exist."""
        holdings = (
            _holding("isin:A", 45.0),
            _holding("isin:B", 45.0),
            _holding("isin:C", 10.0, issuer="OTHER"),
        )
        assert (
            evaluate(Rule("p", Predicate.MAX_POSITION_WEIGHT, 50.0), holdings, today=TODAY).outcome
            is Outcome.PASS
        )
        assert (
            evaluate(Rule("i", Predicate.MAX_ISSUER_WEIGHT, 50.0), holdings, today=TODAY).outcome
            is Outcome.BREACH
        )

    def test_weights_are_measured_against_market_value(self) -> None:
        """A mandate's "5% of the fund" means market value. Face would let a
        deep-discount bond breach a limit it is nowhere near."""
        holdings = (_holding("isin:A", 10.0), _holding("isin:B", 90.0, issuer="OTHER"))
        result = evaluate(Rule("cap", Predicate.MAX_ISSUER_WEIGHT, 50.0), holdings, today=TODAY)
        assert result.outcome is Outcome.BREACH
        assert "90.00%" in result.offenders[0]

    def test_a_limit_exactly_met_is_not_a_breach(self) -> None:
        """The boundary is where it is documented to be. `>` not `>=`: a
        mandate saying "no more than 50%" permits 50%."""
        holdings = (_holding("isin:A", 50.0), _holding("isin:B", 50.0, issuer="OTHER"))
        assert (
            evaluate(Rule("cap", Predicate.MAX_ISSUER_WEIGHT, 50.0), holdings, today=TODAY).outcome
            is Outcome.PASS
        )

    def test_maturity_is_measured_from_today(self) -> None:
        holdings = (_holding("isin:A", 100.0, maturity=date(2075, 1, 1)),)
        assert (
            evaluate(Rule("m", Predicate.MAX_MATURITY_YEARS, 30.0), holdings, today=TODAY).outcome
            is Outcome.BREACH
        )

    def test_a_permitted_list_rejects_what_is_outside_it(self) -> None:
        holdings = (_holding("isin:A", 50.0), _holding("isin:B", 50.0, currency="EUR"))
        result = evaluate(
            Rule("ccy", Predicate.PERMITTED_CURRENCIES, ("USD",)), holdings, today=TODAY
        )
        assert result.outcome is Outcome.BREACH
        assert "isin:B" in result.offenders[0]


class TestRulesAreDataNotCode:
    def test_the_predicate_set_is_closed(self) -> None:
        """An open expression language would make every rule a program, and a
        compliance rule that can do anything cannot be reviewed by the person
        whose mandate it encodes."""
        assert len(Predicate) == 6
        assert all(isinstance(p.value, str) for p in Predicate)

    def test_a_ruleset_is_content_hashed(self) -> None:
        """A breach report names the hash, so a report and the rules that
        produced it cannot drift apart."""
        first = RuleSet("m", (Rule("a", Predicate.MAX_ISSUER_WEIGHT, 5.0),))
        same = RuleSet("m", (Rule("a", Predicate.MAX_ISSUER_WEIGHT, 5.0),))
        assert first.content_hash == same.content_hash

    def test_changing_a_limit_changes_the_hash(self) -> None:
        """The property that makes "we were compliant in March" checkable."""
        first = RuleSet("m", (Rule("a", Predicate.MAX_ISSUER_WEIGHT, 5.0),))
        loosened = RuleSet("m", (Rule("a", Predicate.MAX_ISSUER_WEIGHT, 50.0),))
        assert first.content_hash != loosened.content_hash

    def test_the_hash_is_order_dependent_only_where_order_matters(self) -> None:
        """Rule order is part of the ruleset: a report lists results in the
        order the mandate states them, so two orderings are two documents."""
        one = RuleSet(
            "m",
            (
                Rule("a", Predicate.MAX_ISSUER_WEIGHT, 5.0),
                Rule("b", Predicate.MAX_POSITION_WEIGHT, 5.0),
            ),
        )
        two = RuleSet(
            "m",
            (
                Rule("b", Predicate.MAX_POSITION_WEIGHT, 5.0),
                Rule("a", Predicate.MAX_ISSUER_WEIGHT, 5.0),
            ),
        )
        assert one.content_hash != two.content_hash

    def test_a_report_carries_the_hash_that_produced_it(self) -> None:
        ruleset = RuleSet("m", (Rule("a", Predicate.MAX_ISSUER_WEIGHT, 99.0),))
        assert run(ruleset, _portfolio(), today=TODAY).ruleset_hash == ruleset.content_hash


class TestNoRuleIsSkipped:
    def test_every_rule_produces_a_result(self) -> None:
        """A rule missing from a report is indistinguishable from one that
        passed, and cheaper to produce."""
        ruleset = RuleSet(
            "m",
            (
                Rule("a", Predicate.MAX_ISSUER_WEIGHT, 99.0),
                Rule("b", Predicate.MIN_RATING, "BBB-"),
                Rule("c", Predicate.MAX_MATURITY_YEARS, 1.0),
            ),
        )
        report = run(ruleset, _portfolio(), today=TODAY)
        assert len(report.results) == len(ruleset.rules)
        assert [r.rule.name for r in report.results] == ["a", "b", "c"]


class TestAgainstTheStore:
    """The seam between N-PORT holdings and the rule engine.

    Kept separate from the engine so rules stay unit-testable against
    synthetic portfolios: an engine exercisable only through a store is one
    nobody can write a failing case for.

    These were first written by grepping the function's source for phrases,
    which asserts that a comment exists rather than that the code behaves.
    One of them then failed for the wrong reason — it read `assetCat` in the
    source and concluded the function filtered by it, when the function
    merely *populates* the field the category rule reads. Behaviour is
    cheaper to get right than prose about behaviour.
    """

    @staticmethod
    def _store(tmp_path: Path, rows: list[dict[str, object]]) -> object:
        from datetime import UTC, datetime

        from treble.core.facts import Fact
        from treble.core.provenance import ExtractionMethod, Provenance
        from treble.store.duck import DuckStore

        known = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        store = DuckStore(tmp_path / "m.db")
        record = Provenance(
            source_system="edgar-nport",
            source_uri="https://example.invalid/n",
            retrieved_at=known,
            method=ExtractionMethod.BULK_FILE,
            extractor_version="1",
            payload_hash="a" * 64,
        )
        store.write_provenance([record])
        facts = []
        for row in rows:
            isin = row.pop("isin")
            for field, value in row.items():
                facts.append(
                    Fact(
                        subject=f"isin:{isin}",
                        field=field,
                        value=value,
                        effective_from=TODAY,
                        effective_to=TODAY,
                        knowledge_from=known,
                        provenance_id=record.id,
                    )
                )
        store.write_facts(facts)
        return store

    def test_holdings_without_a_mark_are_dropped_not_guessed(self, tmp_path: Path) -> None:
        """A position with no market value cannot be weighted, and inventing
        one would put a number into every percentage rule in the mandate."""
        from datetime import UTC, datetime

        from treble.tapi.mandate import holdings_from_store

        store = self._store(
            tmp_path,
            [
                {"isin": "US0000000001", "nport:valUSD": 100.0, "nport:assetCat": "DBT"},
                {"isin": "US0000000002", "nport:assetCat": "DBT"},  # no mark
            ],
        )
        holdings = holdings_from_store(store, as_of=datetime(2026, 8, 19, tzinfo=UTC))  # type: ignore[arg-type]
        assert [h.identifier for h in holdings] == ["isin:US0000000001"]

    def test_rating_is_none_rather_than_invented(self, tmp_path: Path) -> None:
        """The difference between a rule that fails honestly and a report
        that lies."""
        from datetime import UTC, datetime

        from treble.tapi.mandate import holdings_from_store

        store = self._store(
            tmp_path, [{"isin": "US0000000001", "nport:valUSD": 100.0, "nport:assetCat": "DBT"}]
        )
        holdings = holdings_from_store(store, as_of=datetime(2026, 8, 19, tzinfo=UTC))  # type: ignore[arg-type]
        assert holdings[0].rating is None

    def test_non_debt_positions_are_not_filtered_out(self, tmp_path: Path) -> None:
        """A mandate covers everything the fund holds. Narrowing to straight
        debt would make more rules evaluable by answering an easier question
        than the one the mandate asks — on the live store that is the
        difference between 686 positions and a comfortable subset."""
        from datetime import UTC, datetime

        from treble.tapi.mandate import holdings_from_store

        store = self._store(
            tmp_path,
            [
                {"isin": "US0000000001", "nport:valUSD": 100.0, "nport:assetCat": "DBT"},
                {"isin": "US0000000002", "nport:valUSD": 50.0, "nport:assetCat": "DE"},
            ],
        )
        holdings = holdings_from_store(store, as_of=datetime(2026, 8, 19, tzinfo=UTC))  # type: ignore[arg-type]
        assert len(holdings) == 2
        assert {h.asset_category for h in holdings} == {"DBT", "DE"}

    def test_a_mixed_portfolio_reports_unevaluable_rather_than_clean(self, tmp_path: Path) -> None:
        """The whole point, end to end. A derivative record does not populate
        the fields a bond rule reads, so a mandate over a mixed portfolio
        must say so — not print PASS for the rules it could not test."""
        from datetime import UTC, datetime

        from treble.tapi.mandate import check_mandate

        store = self._store(
            tmp_path,
            [
                {
                    "isin": "US0000000001",
                    "nport:valUSD": 100.0,
                    "nport:assetCat": "DBT",
                    "nport:curCd": "USD",
                },
                {"isin": "US0000000002", "nport:valUSD": 50.0, "nport:assetCat": "DE"},
            ],
        )
        ruleset = RuleSet(
            "mixed",
            (
                Rule("ccy", Predicate.PERMITTED_CURRENCIES, ("USD",)),
                Rule("rating", Predicate.MIN_RATING, "BBB-"),
            ),
        )
        report = check_mandate(store, ruleset, as_of=datetime(2026, 8, 19, tzinfo=UTC))  # type: ignore[arg-type]
        assert report.clean is False
        assert len(report.not_evaluable) == 2
