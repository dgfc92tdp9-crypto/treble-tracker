"""Mandate files, and every way one can be wrong.

`rules.py` exists because a rule silently missing from a report produces a
clean bill of health on a portfolio nobody checked. A rule silently
dropped at *load* time does the same damage one step earlier and is
harder to notice, because the report will not even name the rule that
went missing.

So every test here is a refusal. The happy path gets four lines; the rest
of the file is the shapes a mandate can take that would otherwise load as
a smaller mandate than its author wrote.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from treble.compliance.loader import (
    MandateError,
    available,
    load_named,
    load_ruleset,
)
from treble.compliance.rules import Predicate

GOOD = """
name: Test Mandate
rules:
  - name: No issuer above 10%
    predicate: max_issuer_weight
    limit: 10.0
  - name: USD only
    predicate: permitted_currencies
    limit: [USD]
"""


def _write(tmp_path: Path, text: str, stem: str = "m") -> Path:
    path = tmp_path / f"{stem}.yaml"
    path.write_text(text)
    return path


class TestALoadedMandate:
    def test_it_becomes_the_ruleset_the_engine_evaluates(self, tmp_path: Path) -> None:
        ruleset = load_ruleset(_write(tmp_path, GOOD))
        assert ruleset.name == "Test Mandate"
        assert [r.name for r in ruleset.rules] == ["No issuer above 10%", "USD only"]
        assert ruleset.rules[0].predicate is Predicate.MAX_ISSUER_WEIGHT
        assert ruleset.rules[0].limit == 10.0
        assert ruleset.rules[1].limit == ("USD",)

    def test_a_single_currency_may_be_written_without_a_list(self, tmp_path: Path) -> None:
        """`currencies: USD` is what somebody writes for a single-currency
        mandate. The ambiguity a list resolves does not exist for one
        item, so refusing it would be pedantry rather than safety."""
        text = "name: M\nrules:\n  - {name: R, predicate: permitted_currencies, limit: USD}\n"
        assert load_ruleset(_write(tmp_path, text)).rules[0].limit == ("USD",)


class TestTheHashTracksTheRulesNotTheFile:
    def test_reformatting_does_not_change_the_hash(self, tmp_path: Path) -> None:
        """A hash over the bytes would make every whitespace edit look
        like a mandate change, and teach a reader to ignore the field."""
        other = """
name: Test Mandate      # a comment
rules:
  - predicate: max_issuer_weight
    name: "No issuer above 10%"
    limit: 10.00
  - {name: USD only, predicate: permitted_currencies, limit: ["USD"]}
"""
        assert (
            load_ruleset(_write(tmp_path, GOOD, "a")).content_hash
            == load_ruleset(_write(tmp_path, other, "b")).content_hash
        )

    def test_changing_a_limit_changes_the_hash(self, tmp_path: Path) -> None:
        """The other half. A hash that ignored the limits would let a
        mandate be loosened without any report showing it."""
        loosened = GOOD.replace("limit: 10.0", "limit: 25.0")
        assert (
            load_ruleset(_write(tmp_path, GOOD, "a")).content_hash
            != load_ruleset(_write(tmp_path, loosened, "b")).content_hash
        )

    def test_reordering_rules_changes_the_hash(self, tmp_path: Path) -> None:
        """Order is part of the document a compliance officer signed, and
        `Report` prints results in outcome order regardless — so the hash
        is the only thing that would notice a reordering."""
        swapped = """
name: Test Mandate
rules:
  - {name: USD only, predicate: permitted_currencies, limit: [USD]}
  - {name: No issuer above 10%, predicate: max_issuer_weight, limit: 10.0}
"""
        assert (
            load_ruleset(_write(tmp_path, GOOD, "a")).content_hash
            != load_ruleset(_write(tmp_path, swapped, "b")).content_hash
        )


class TestEveryRefusal:
    """None of these may produce a partial ruleset."""

    def test_an_unknown_predicate_names_the_ones_that_exist(self, tmp_path: Path) -> None:
        text = "name: M\nrules:\n  - {name: R, predicate: max_sector_weight, limit: 10}\n"
        with pytest.raises(MandateError, match="unknown predicate") as caught:
            load_ruleset(_write(tmp_path, text))
        assert "max_issuer_weight" in str(caught.value), "the message must list what is valid"

    def test_an_empty_rule_list_is_refused(self, tmp_path: Path) -> None:
        """The most dangerous document this loader could produce: a
        mandate with no rules evaluates clean against any portfolio."""
        with pytest.raises(MandateError, match="no rules"):
            load_ruleset(_write(tmp_path, "name: M\nrules: []\n"))

    def test_a_missing_rule_list_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(MandateError, match="no rules"):
            load_ruleset(_write(tmp_path, "name: M\n"))

    def test_a_missing_limit_is_refused(self, tmp_path: Path) -> None:
        text = "name: M\nrules:\n  - {name: R, predicate: max_issuer_weight}\n"
        with pytest.raises(MandateError, match="needs a `limit`"):
            load_ruleset(_write(tmp_path, text))

    def test_a_nameless_rule_is_refused(self, tmp_path: Path) -> None:
        """A rule with no name produces a report row nobody can act on."""
        text = "name: M\nrules:\n  - {predicate: max_issuer_weight, limit: 10}\n"
        with pytest.raises(MandateError, match="non-empty `name`"):
            load_ruleset(_write(tmp_path, text))

    def test_two_rules_with_one_name_are_refused(self, tmp_path: Path) -> None:
        """A reader cannot tell which constraint a PASS refers to, and the
        second silently shadows the first in any lookup by name."""
        text = (
            "name: M\nrules:\n"
            "  - {name: R, predicate: max_issuer_weight, limit: 10}\n"
            "  - {name: R, predicate: max_position_weight, limit: 5}\n"
        )
        with pytest.raises(MandateError, match="two rules named"):
            load_ruleset(_write(tmp_path, text))

    def test_a_nameless_mandate_is_refused(self, tmp_path: Path) -> None:
        text = "rules:\n  - {name: R, predicate: max_issuer_weight, limit: 10}\n"
        with pytest.raises(MandateError, match="non-empty `name`"):
            load_ruleset(_write(tmp_path, text))

    def test_unreadable_yaml_names_the_file(self, tmp_path: Path) -> None:
        with pytest.raises(MandateError, match="not readable as YAML"):
            load_ruleset(_write(tmp_path, "name: [unclosed\n"))

    def test_a_list_at_the_top_level_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(MandateError, match="mapping at the top level"):
            load_ruleset(_write(tmp_path, "- name: M\n"))


class TestTheLimitMustFitThePredicate:
    """A limit of the wrong shape would reach `evaluate` and either fail
    there or, worse, compare in a way Python permits and the mandate's
    author did not intend."""

    @pytest.mark.parametrize(
        "predicate", ["max_issuer_weight", "max_position_weight", "max_maturity_years"]
    )
    def test_a_numeric_predicate_refuses_a_list(self, tmp_path: Path, predicate: str) -> None:
        text = f"name: M\nrules:\n  - {{name: R, predicate: {predicate}, limit: [10, 20]}}\n"
        with pytest.raises(MandateError, match="needs a number"):
            load_ruleset(_write(tmp_path, text))

    def test_a_numeric_predicate_refuses_a_string(self, tmp_path: Path) -> None:
        text = 'name: M\nrules:\n  - {name: R, predicate: max_issuer_weight, limit: "ten"}\n'
        with pytest.raises(MandateError, match="needs a number"):
            load_ruleset(_write(tmp_path, text))

    def test_a_numeric_predicate_refuses_a_boolean(self, tmp_path: Path) -> None:
        """`bool` is a subclass of `int`, so `limit: true` would otherwise
        load as a limit of 1% — a mandate far tighter than anyone wrote,
        which would breach on almost every holding and be read as a data
        problem rather than a typo."""
        text = "name: M\nrules:\n  - {name: R, predicate: max_issuer_weight, limit: true}\n"
        with pytest.raises(MandateError, match="needs a number"):
            load_ruleset(_write(tmp_path, text))

    def test_a_membership_predicate_refuses_a_number(self, tmp_path: Path) -> None:
        text = "name: M\nrules:\n  - {name: R, predicate: permitted_currencies, limit: 10}\n"
        with pytest.raises(MandateError, match="non-empty list"):
            load_ruleset(_write(tmp_path, text))

    def test_a_membership_predicate_refuses_an_empty_list(self, tmp_path: Path) -> None:
        """Permitting nothing would breach on every holding, which reads
        as a portfolio problem rather than an unfinished mandate."""
        text = "name: M\nrules:\n  - {name: R, predicate: permitted_currencies, limit: []}\n"
        with pytest.raises(MandateError, match="non-empty list"):
            load_ruleset(_write(tmp_path, text))

    def test_a_membership_predicate_refuses_mixed_types(self, tmp_path: Path) -> None:
        text = "name: M\nrules:\n  - {name: R, predicate: permitted_currencies, limit: [USD, 5]}\n"
        with pytest.raises(MandateError, match="list of strings"):
            load_ruleset(_write(tmp_path, text))

    def test_a_rating_floor_refuses_a_number(self, tmp_path: Path) -> None:
        text = "name: M\nrules:\n  - {name: R, predicate: min_rating, limit: 3}\n"
        with pytest.raises(MandateError, match="non-empty string"):
            load_ruleset(_write(tmp_path, text))

    def test_an_unclassified_predicate_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fallthrough in `_limit` cannot fire while every predicate is
        classified, so deleting it passed every other test here.

        It is kept because the function must be total — without it `_limit`
        falls off the end and a new predicate would accept a limit of any
        shape at all, which is the loose behaviour this module exists to
        prevent. Unclassifying one predicate is what makes the branch
        reachable, and `test_every_predicate_has_a_declared_limit_shape`
        below is what stops that state reaching a build.
        """
        from treble.compliance import loader

        monkeypatch.setattr(loader, "_NUMERIC", frozenset())
        text = "name: M\nrules:\n  - {name: R, predicate: max_issuer_weight, limit: 10}\n"
        with pytest.raises(MandateError, match="no declared limit shape"):
            loader.load_ruleset(_write(tmp_path, text))

    def test_every_predicate_has_a_declared_limit_shape(self) -> None:
        """The guard for a predicate added to the enum and not classified
        here, which would otherwise accept any limit at all. Asserted over
        the enum so it fails when the enum grows, not when someone
        remembers to update a list."""
        from treble.compliance.loader import _MEMBERSHIP, _NUMERIC, _SCALAR_TEXT

        classified = _NUMERIC | _MEMBERSHIP | _SCALAR_TEXT
        assert set(Predicate) == classified, (
            f"unclassified predicate(s): {sorted(p.value for p in set(Predicate) - classified)}"
        )


class TestLoadingByName:
    def test_a_missing_mandate_lists_what_exists(self, tmp_path: Path) -> None:
        _write(tmp_path, GOOD, "balanced")
        with pytest.raises(MandateError, match="balanced"):
            load_named("nosuch", tmp_path)

    def test_available_is_sorted(self, tmp_path: Path) -> None:
        for stem in ("zeta", "alpha", "mid"):
            _write(tmp_path, GOOD, stem)
        assert [p.stem for p in available(tmp_path)] == ["alpha", "mid", "zeta"]

    def test_a_directory_that_does_not_exist_is_empty_not_an_error(self, tmp_path: Path) -> None:
        assert available(tmp_path / "absent") == ()


class TestTheShippedMandate:
    """The file in `config/mandates/` is part of the deliverable, not a
    fixture: it is what a reader opens to see what a mandate looks like."""

    def test_it_loads(self) -> None:
        ruleset = load_named("balanced-income")
        assert len(ruleset.rules) == 5

    def test_it_includes_a_rule_this_store_cannot_evaluate(self) -> None:
        """Deliberate. A mandate curated until every rule passes tests the
        curation rather than the portfolio, and MIN_RATING is evaluable
        nowhere in this repository — so the shipped example must carry one
        to show what NOT EVALUABLE looks like."""
        ruleset = load_named("balanced-income")
        assert any(r.predicate is Predicate.MIN_RATING for r in ruleset.rules)
