"""Version-controlled, unit-testable compliance rules (P3_4).

**A rule that cannot be evaluated must never report a pass.** That is the
whole design, and everything else follows from it.

A compliance engine that silently skips a rule it lacks data for produces a
clean report, and a clean report is what a portfolio manager acts on and a
compliance officer signs. "No holdings rated below BBB-" evaluated against a
store with no ratings is not compliant — it is unchecked — and the two are
indistinguishable on a screen that prints PASS for both. So `NOT_EVALUABLE`
is a distinct outcome, it is *not* a pass, and a run containing one is not a
clean run.

This repository has met the same shape three times already: an analytic with
no data reporting a price, a similarity metric silently dropping two of its
three dimensions, and a product catalogue claiming "HICP stored" on a store
holding none. Here the cost of getting it wrong is a mandate breach nobody
was told about.

**Rules are data, never code.** The predicate set is closed — an enum, not
an expression — for the reason the screen contract's conditional attributes
are closed: `eval` on a rule file makes every rule a program, and a
compliance rule that can do anything cannot be reviewed by the person whose
mandate it encodes. It also means a rule is comparable, hashable, and
diffable, which is what "version-controlled" has to mean if it is to mean
anything.

**Rules are content-hashed.** A breach report names the hash of the ruleset
that produced it, so a report and the rules that made it cannot drift apart.
Without that, "we were compliant in March" is a claim about a file nobody
can reproduce.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date

#: How much of the portfolio a percentage limit is measured against. Market
#: value, because that is what a mandate means by "5% of the fund" — face
#: would let a discount bond breach a limit it is nowhere near.
BASIS = "market value"


class Predicate(enum.Enum):
    """The closed set of things a rule can test.

    Closed on purpose. An open expression language would make every rule a
    program, and a compliance rule that can do anything cannot be reviewed
    by the person whose mandate it encodes.
    """

    #: No single issuer above a percentage of the portfolio.
    MAX_ISSUER_WEIGHT = "max_issuer_weight"
    #: No single position above a percentage of the portfolio.
    MAX_POSITION_WEIGHT = "max_position_weight"
    #: Nothing maturing beyond a horizon.
    MAX_MATURITY_YEARS = "max_maturity_years"
    #: Every holding must be in one of the permitted currencies.
    PERMITTED_CURRENCIES = "permitted_currencies"
    #: Every holding must be in one of the permitted asset categories.
    PERMITTED_ASSET_CATEGORIES = "permitted_asset_categories"
    #: Nothing rated below a floor. Present deliberately and evaluable
    #: nowhere: no rating source this repository may use has been found, and
    #: a mandate that cares about ratings should see NOT EVALUABLE rather
    #: than a rule quietly missing from its report.
    MIN_RATING = "min_rating"


class Outcome(enum.Enum):
    #: Ruff reads a constant named PASS as a credential; it is an outcome.
    PASS = "pass"  # noqa: S105
    BREACH = "breach"
    #: The rule could not be tested. **Not a pass.**
    NOT_EVALUABLE = "not evaluable"


@dataclass(frozen=True)
class Holding:
    """One position, as a rule sees it."""

    identifier: str
    market_value: float
    issuer: str | None = None
    maturity: date | None = None
    currency: str | None = None
    asset_category: str | None = None
    rating: str | None = None


@dataclass(frozen=True)
class Rule:
    """One testable constraint."""

    name: str
    predicate: Predicate
    #: Percentage for weight rules, years for maturity, a list for the
    #: membership rules. Kept as a plain value so a rule stays comparable
    #: and diffable.
    limit: float | tuple[str, ...] | str

    def canonical(self) -> dict[str, object]:
        limit = list(self.limit) if isinstance(self.limit, tuple) else self.limit
        return {"name": self.name, "predicate": self.predicate.value, "limit": limit}


@dataclass(frozen=True)
class Result:
    """What one rule said, and why."""

    rule: Rule
    outcome: Outcome
    detail: str
    #: Positions responsible, for a breach or for the gap that made the rule
    #: unevaluable. A breach nobody can attribute to a holding is one nobody
    #: can act on.
    offenders: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        """True only for a pass. `NOT_EVALUABLE` is not clean."""
        return self.outcome is Outcome.PASS


@dataclass(frozen=True)
class RuleSet:
    """A mandate's rules, identified by their content."""

    name: str
    rules: tuple[Rule, ...] = field(default_factory=tuple)

    @property
    def content_hash(self) -> str:
        """SHA-256 over the canonical rules.

        A breach report names this, so a report and the rules that produced
        it cannot drift apart. Without it, "we were compliant in March" is a
        claim about a file nobody can reproduce.
        """
        payload = json.dumps(
            {"name": self.name, "rules": [rule.canonical() for rule in self.rules]},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()


#: N-PORT asset categories whose instruments carry a maturity date. Taken
#: from the filings rather than invented: every DBT and ABS-* holding in
#: the live store reports one, and no EC, EP or DE holding does. `STIV`
#: (short-term investment vehicle) is deliberately absent — 6 of 8 in the
#: store report no maturity, because the category covers money-market
#: *funds* as well as instruments, so including it would reintroduce the
#: permanent NOT EVALUABLE this scoping exists to remove.
MATURING_CATEGORIES = frozenset({"DBT", "ABS-O", "ABS-MBS", "ABS-CBDO", "ABS-CDO", "ABS-ABS"})


def _matures(holding: Holding) -> bool:
    """Whether a maturity rule applies to this holding at all.

    An unknown category is treated as maturing *if it reports a maturity*
    and non-maturing otherwise, so a category this list has not seen
    cannot silently drop a bond out of scope — the conservative direction,
    since a bond excluded from a maturity test is the failure that matters.
    """
    if holding.asset_category in MATURING_CATEGORIES:
        return True
    return holding.asset_category is None and holding.maturity is not None


def _weight(value: float, total: float) -> float:
    return 0.0 if total <= 0 else value / total * 100.0


def evaluate(rule: Rule, holdings: tuple[Holding, ...], *, today: date) -> Result:
    """Test one rule, or say it could not be tested.

    Every branch that lacks data returns `NOT_EVALUABLE` naming the missing
    field and the positions it is missing from. A rule that returned PASS
    for the same reason would be reporting compliance nobody checked.
    """
    total = sum(holding.market_value for holding in holdings)
    if not holdings:
        return Result(
            rule,
            Outcome.NOT_EVALUABLE,
            "no holdings to test; an empty portfolio breaches nothing and proves nothing",
        )

    if rule.predicate is Predicate.MIN_RATING:
        unrated = tuple(h.identifier for h in holdings if h.rating is None)
        if unrated:
            return Result(
                rule,
                Outcome.NOT_EVALUABLE,
                f"{len(unrated)} of {len(holdings)} holdings carry no rating. No rating "
                "source this repository may use has been found, so this rule cannot be "
                "tested — which is not the same as passing it",
                unrated[:5],
            )

    if rule.predicate in (Predicate.MAX_ISSUER_WEIGHT, Predicate.MAX_POSITION_WEIGHT):
        limit = float(rule.limit)  # type: ignore[arg-type]
        if rule.predicate is Predicate.MAX_ISSUER_WEIGHT:
            # Unattributed holdings do not automatically make the rule
            # unevaluable — only if they could change the answer.
            #
            # Refusing whenever any holding lacks an issuer is correct but
            # far too strong: on the live fund one position out of 685,
            # worth 0.03%, blocked a rule that 684 holdings could answer.
            # A rule that refuses over a rounding error trains the reader
            # to skim past NOT EVALUABLE, which is the outcome this module
            # most needs them to read.
            #
            # So bound it. If some issuer already exceeds the limit, the
            # unattributed weight cannot un-breach it. If the largest
            # attributed issuer plus *all* the unattributed weight still
            # sits inside the limit, no assignment of those holdings can
            # breach it. Only between those two is the answer genuinely
            # unknown, and that is the only case that refuses.
            missing = tuple(h.identifier for h in holdings if h.issuer is None)
            unattributed = _weight(sum(h.market_value for h in holdings if h.issuer is None), total)
            grouped: dict[str, float] = {}
            for holding in holdings:
                if holding.issuer is None:
                    continue
                grouped[holding.issuer] = grouped.get(holding.issuer, 0.0) + holding.market_value
            breaches = tuple(
                f"{issuer} {_weight(value, total):.2f}%"
                for issuer, value in sorted(grouped.items())
                if _weight(value, total) > limit
            )
            if not breaches and missing:
                largest = max((_weight(v, total) for v in grouped.values()), default=0.0)
                if largest + unattributed > limit:
                    return Result(
                        rule,
                        Outcome.NOT_EVALUABLE,
                        f"{len(missing)} holding(s) carry no issuer, worth "
                        f"{unattributed:.2f}%; the largest attributed issuer is "
                        f"{largest:.2f}%, so the {limit:g}% limit cannot be settled",
                        missing[:5],
                    )
        else:
            breaches = tuple(
                f"{h.identifier} {_weight(h.market_value, total):.2f}%"
                for h in holdings
                if _weight(h.market_value, total) > limit
            )
        if breaches:
            return Result(
                rule,
                Outcome.BREACH,
                f"{len(breaches)} above the {limit:g}% limit, by {BASIS}",
                breaches,
            )
        return Result(rule, Outcome.PASS, f"all within {limit:g}% by {BASIS}")

    if rule.predicate is Predicate.MAX_MATURITY_YEARS:
        limit = float(rule.limit)  # type: ignore[arg-type]
        # Scoped to instruments that *have* a maturity. An equity does not
        # mature, and that is not missing data — no amount of ingest will
        # ever supply it. Testing the rule over every holding made it
        # permanently NOT EVALUABLE on any portfolio containing a share,
        # which on the live fund was 436 of 685 positions, so a rule that
        # could genuinely have fired on the 239 bonds never ran.
        #
        # A permanently unevaluable rule is worse than a missing one: it
        # teaches the reader that NOT EVALUABLE is background noise, and
        # the next one they skim past is a rule that really could not be
        # tested.
        applicable = tuple(h for h in holdings if _matures(h))
        if not applicable:
            return Result(
                rule,
                Outcome.NOT_EVALUABLE,
                f"no holding carries a maturity: none of {len(holdings)} is a debt instrument",
            )
        missing = tuple(h.identifier for h in applicable if h.maturity is None)
        if missing:
            return Result(
                rule,
                Outcome.NOT_EVALUABLE,
                f"{len(missing)} of {len(applicable)} debt holding(s) report no maturity",
                missing[:5],
            )
        out_of_scope = len(holdings) - len(applicable)
        scope = f" over {len(applicable)} debt holding(s); {out_of_scope} do not mature"
        breaches = tuple(
            h.identifier
            for h in applicable
            if h.maturity is not None and (h.maturity - today).days / 365.25 > limit
        )
        if breaches:
            return Result(
                rule,
                Outcome.BREACH,
                f"{len(breaches)} maturing beyond {limit:g}y,{scope}",
                breaches,
            )
        return Result(rule, Outcome.PASS, f"none maturing beyond {limit:g}y,{scope}")

    permitted = tuple(str(v).upper() for v in rule.limit)  # type: ignore[union-attr]
    currencies = rule.predicate is Predicate.PERMITTED_CURRENCIES

    def getter(holding: Holding) -> str | None:
        return holding.currency if currencies else holding.asset_category

    label = "currency" if currencies else "asset category"
    missing = tuple(h.identifier for h in holdings if getter(h) is None)
    if missing:
        return Result(
            rule, Outcome.NOT_EVALUABLE, f"{len(missing)} holding(s) report no {label}", missing[:5]
        )
    breaches = tuple(
        f"{h.identifier} {getter(h)}" for h in holdings if str(getter(h)).upper() not in permitted
    )
    if breaches:
        return Result(
            rule, Outcome.BREACH, f"{len(breaches)} outside {', '.join(permitted)}", breaches
        )
    return Result(rule, Outcome.PASS, f"all within {', '.join(permitted)}")


@dataclass(frozen=True)
class Report:
    """A full run of one ruleset against one portfolio."""

    ruleset: str
    ruleset_hash: str
    as_of: date
    results: tuple[Result, ...]

    @property
    def breaches(self) -> tuple[Result, ...]:
        return tuple(r for r in self.results if r.outcome is Outcome.BREACH)

    @property
    def not_evaluable(self) -> tuple[Result, ...]:
        return tuple(r for r in self.results if r.outcome is Outcome.NOT_EVALUABLE)

    @property
    def clean(self) -> bool:
        """True only when every rule passed.

        A run with an unevaluable rule is **not** clean. That is the single
        line this module exists for: a clean report is what somebody acts on
        and signs, and one containing a rule nobody could test is a claim
        about rules rather than about the portfolio.
        """
        return all(result.clean for result in self.results)


def run(ruleset: RuleSet, holdings: tuple[Holding, ...], *, today: date) -> Report:
    """Evaluate every rule. No rule is skipped, ever."""
    return Report(
        ruleset=ruleset.name,
        ruleset_hash=ruleset.content_hash,
        as_of=today,
        results=tuple(evaluate(rule, holdings, today=today) for rule in ruleset.rules),
    )


__all__ = [
    "BASIS",
    "MATURING_CATEGORIES",
    "Holding",
    "Outcome",
    "Predicate",
    "Report",
    "Result",
    "Rule",
    "RuleSet",
    "evaluate",
    "run",
]
