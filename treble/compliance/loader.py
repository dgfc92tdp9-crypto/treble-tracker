"""Mandates as version-controlled files.

The criterion is a compliance DSL that is *version-controlled* and
*unit-testable*. Rules built in Python satisfied the second and not the
first: a mandate encoded in code cannot be reviewed by the person whose
mandate it is, diffed in a pull request, or pointed at by an auditor
asking what the rules were in March. So a ruleset is a YAML file in
`config/mandates/`, and this turns it into the same `RuleSet` the engine
already evaluates.

**Everything here refuses rather than skips, and that is the whole
module.** `rules.py` exists because a rule silently missing from a report
produces a clean bill of health on a portfolio nobody checked; a rule
silently dropped at *load* time does exactly the same damage one step
earlier, and is harder to notice because the report will not even name
it. A file with a typo in a predicate is not a mandate with one fewer
rule — it is a file whose author believed a constraint was being
enforced.

So: an unknown predicate raises, a missing limit raises, a limit of the
wrong shape for its predicate raises, two rules sharing a name raise, and
a file with no rules raises. None of those produce a partial ruleset.

**The hash is over the rules, not the file.** `RuleSet.content_hash`
canonicalises the rules themselves, so reformatting the YAML — reordering
keys, changing quoting, adding a comment — leaves a report's hash
unchanged, while changing a limit changes it. A hash over the bytes would
make every whitespace edit look like a mandate change and teach readers
to ignore it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from treble.compliance.rules import Predicate, Rule, RuleSet

#: Where mandates live. Under `config/` with the universe definitions,
#: because both answer "what did this install consider in scope", and both
#: belong in review rather than in a database.
MANDATE_DIR = Path(__file__).resolve().parents[2] / "config" / "mandates"

#: Predicates whose limit is a single number: a percentage or a count of
#: years. A list here would be a mandate saying "no more than [50, 60]%".
_NUMERIC = frozenset(
    {
        Predicate.MAX_ISSUER_WEIGHT,
        Predicate.MAX_POSITION_WEIGHT,
        Predicate.MAX_MATURITY_YEARS,
    }
)

#: Predicates whose limit is a set of permitted values. A bare string is
#: accepted and wrapped, because `currencies: USD` is what somebody writes
#: for a single-currency mandate and refusing it would be pedantry rather
#: than safety — the ambiguity a list resolves does not exist for one item.
_MEMBERSHIP = frozenset({Predicate.PERMITTED_CURRENCIES, Predicate.PERMITTED_ASSET_CATEGORIES})

#: Predicates whose limit is one string: a rating floor such as "BBB-".
_SCALAR_TEXT = frozenset({Predicate.MIN_RATING})


class MandateError(ValueError):
    """A mandate file could not be read as a complete set of rules."""


def load_ruleset(path: Path) -> RuleSet:
    """One mandate file -> one `RuleSet`, or an exception.

    Never a partial ruleset. Every refusal below names the file and the
    rule, because a mandate is edited by somebody who is not looking at
    this code and needs to know which line to fix.
    """
    try:
        document = yaml.safe_load(path.read_text())
    except yaml.YAMLError as error:
        raise MandateError(f"{path.name}: not readable as YAML: {error}") from error
    if not isinstance(document, dict):
        raise MandateError(f"{path.name}: expected a mapping at the top level")

    name = document.get("name")
    if not isinstance(name, str) or not name.strip():
        raise MandateError(f"{path.name}: needs a non-empty `name`")

    raw_rules = document.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        # A mandate with no rules evaluates clean against any portfolio.
        # That is the single most dangerous document this loader could
        # produce, and it is what an empty `rules:` key silently means.
        raise MandateError(
            f"{path.name}: has no rules. An empty mandate reports clean against "
            "any portfolio, which is worse than having no mandate at all"
        )

    rules: list[Rule] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_rules, start=1):
        rule = _rule(raw, path=path, index=index)
        if rule.name in seen:
            # Two rules under one name make a report ambiguous: a reader
            # cannot tell which constraint the PASS or the BREACH refers
            # to, and the second silently shadows the first in any lookup.
            raise MandateError(f"{path.name}: two rules named {rule.name!r}")
        seen.add(rule.name)
        rules.append(rule)

    return RuleSet(name=name, rules=tuple(rules))


def _rule(raw: Any, *, path: Path, index: int) -> Rule:
    where = f"{path.name}: rule {index}"
    if not isinstance(raw, dict):
        raise MandateError(f"{where}: expected a mapping")

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise MandateError(f"{where}: needs a non-empty `name`")

    predicate_name = raw.get("predicate")
    try:
        predicate = Predicate(predicate_name)
    except ValueError as error:
        # The closed predicate set is the reason rules are reviewable, so
        # an unknown one is a hard stop and the message lists what exists.
        # Skipping it would leave the author believing a constraint was
        # enforced.
        raise MandateError(
            f"{where} ({name!r}): unknown predicate {predicate_name!r}. "
            f"Known: {', '.join(sorted(p.value for p in Predicate))}"
        ) from error

    if "limit" not in raw:
        raise MandateError(f"{where} ({name!r}): needs a `limit`")
    return Rule(name=name, predicate=predicate, limit=_limit(raw["limit"], predicate, where, name))


def _limit(
    value: Any, predicate: Predicate, where: str, name: str
) -> float | tuple[str, ...] | str:
    """Check the limit's shape against its predicate.

    A percentage written as a list, or a currency list written as a
    number, would reach `evaluate` and fail there — or worse, compare in a
    way Python permits and the mandate's author did not intend.
    """
    if predicate in _NUMERIC:
        # bool first: it is a subclass of int, and `limit: true` is a
        # mistake that would otherwise become a limit of 1%.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MandateError(
                f"{where} ({name!r}): {predicate.value} needs a number, got {value!r}"
            )
        return float(value)

    if predicate in _MEMBERSHIP:
        items = [value] if isinstance(value, str) else value
        if not isinstance(items, list) or not items:
            raise MandateError(
                f"{where} ({name!r}): {predicate.value} needs a non-empty list, got {value!r}"
            )
        if not all(isinstance(item, str) for item in items):
            raise MandateError(f"{where} ({name!r}): {predicate.value} needs a list of strings")
        return tuple(items)

    if predicate in _SCALAR_TEXT:
        if not isinstance(value, str) or not value.strip():
            raise MandateError(
                f"{where} ({name!r}): {predicate.value} needs a non-empty string, got {value!r}"
            )
        return value

    # Unreachable while every predicate is in exactly one of the sets
    # above, and checked rather than assumed: adding a predicate to the
    # enum without classifying it here would otherwise accept any limit
    # shape at all, which is the loose behaviour this module exists to
    # prevent.
    raise MandateError(f"{where} ({name!r}): {predicate.value} has no declared limit shape")


def available(directory: Path | None = None) -> tuple[Path, ...]:
    """Mandate files on disk, sorted so a listing is stable.

    The default is resolved at *call* time rather than written as
    `directory: Path = MANDATE_DIR`. A default argument is bound when the
    module is imported, so the module-level constant could never be
    redirected afterwards — by a caller pointing at another directory, or
    by a test. Found by a test that patched `MANDATE_DIR` and watched it
    have no effect whatsoever.
    """
    directory = MANDATE_DIR if directory is None else directory
    if not directory.is_dir():
        return ()
    return tuple(sorted(directory.glob("*.yaml")))


def load_named(mandate: str, directory: Path | None = None) -> RuleSet:
    """Load by file stem, naming what exists when it is not found."""
    directory = MANDATE_DIR if directory is None else directory
    path = directory / f"{mandate}.yaml"
    if not path.exists():
        known = ", ".join(p.stem for p in available(directory)) or "none"
        raise MandateError(f"no mandate {mandate!r} in {directory}. Available: {known}")
    return load_ruleset(path)


__all__ = [
    "MANDATE_DIR",
    "MandateError",
    "available",
    "load_named",
    "load_ruleset",
]
