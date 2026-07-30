"""Planning and execution for TQL (spec §4.2).

A parsed query says what is wanted. Planning turns it into what will be
done — a universe to select and a set of field reads, with every relative
date resolved against one `as_of`. Execution runs that plan against the
store and the analytics engines.

**Why this layer talks to the store directly.** The architecture puts `tql`
*below* `tapi`: TAPI exposes TQL to screens, so TQL cannot call TAPI without
inverting the dependency. That is the spec's own description — a query
"compiles to a DuckDB execution plan plus analytics-engine calls" — and it
is why I7 is not violated here. Screens still reach TQL only through TAPI.

**Planning is separate from execution** so a plan can be inspected, costed
and shown before anything runs. It is also what makes the resolved dates
visible: `range(-1Y, 0D)` becomes two real dates, once, so every field in
the query sees the same window rather than each resolving its own clock.

**An unsupported universe raises.** Returning no rows for a universe this
system cannot yet select would be indistinguishable from a universe that is
genuinely empty, and a screen would render an honest-looking table of
nothing.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.tql.grammar import Comparison, Predicate, Query, Value


class TqlExecutionError(RuntimeError):
    """The query is valid TQL but cannot be run as asked."""


class ModelSource(Protocol):
    """The analytics side of a query.

    Injected rather than imported: the field dictionary that knows which
    mnemonics are model-derived lives in `tapi`, which sits *above* `tql`.
    So TAPI supplies this, and TQL stays able to run against stored facts
    alone — which is also what lets the executor be tested without QuantLib.

    ``compute`` returns None for a mnemonic it does not handle, so an
    unwired model falls through to the store rather than becoming a null.
    """

    def compute(
        self,
        subject: TUID,
        mnemonic: str,
        overrides: tuple[tuple[str, Value], ...],
        *,
        as_of: datetime,
    ) -> tuple[object, str | None] | None: ...


class FactSource(Protocol):
    """The slice of the store a plan needs.

    A protocol rather than the concrete store so a plan can be executed
    against recorded facts in a test without a database.
    """

    def read(self, subject: TUID, field: str, *, as_of: datetime) -> list[Fact]: ...

    def subjects_with_prefix(self, prefix: str, *, as_of: datetime) -> list[TUID]: ...


#: Selector name -> the subject namespace it draws from. Closed, because a
#: selector this system cannot resolve must raise rather than quietly
#: return nothing.
_UNIVERSES: dict[str, str] = {
    "bonds": "cusip:",
    "filers": "cik:",
    "series": "fred:",
}


class FieldRequest(BaseModel):
    """One field to retrieve, with the assumptions it is computed under."""

    model_config = ConfigDict(frozen=True)

    #: As written in the query. TQL is conventionally lower-case, but
    #: documented mnemonics are upper-case (`PX_LAST`) while as-reported
    #: source tags keep the source's own casing (`int_rate`,
    #: `us-gaap:Assets:USD`). The query text alone cannot say which, so both
    #: spellings are tried rather than one being assumed — guessing wrong
    #: returns an empty column that reads as "not reported".
    name: str
    mnemonic: str
    overrides: tuple[tuple[str, Value], ...] = ()

    @property
    def candidates(self) -> tuple[str, ...]:
        return (self.name,) if self.name == self.mnemonic else (self.name, self.mnemonic)


class Plan(BaseModel):
    """What a query will actually do, with every date already resolved."""

    model_config = ConfigDict(frozen=True)

    subject_prefix: str
    predicates: tuple[Predicate, ...]
    fields: tuple[FieldRequest, ...]
    as_of: datetime
    #: Resolved from `with(dates=range(...))`; None means point-in-time only.
    window: tuple[date, date] | None = None

    def explain(self) -> str:
        """The plan in one line, for showing a user before it runs."""
        filters = ", ".join(
            f"{p.attribute} {p.comparison.value} {p.value!r}" for p in self.predicates
        )
        window = f" over {self.window[0]}..{self.window[1]}" if self.window else ""
        return (
            f"read {', '.join(f.name for f in self.fields)} "
            f"for {self.subject_prefix}* where ({filters or 'any'}){window} "
            f"as of {self.as_of.date()}"
        )


class Row(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject: str
    values: tuple[tuple[str, object | None], ...]
    #: Field names whose value came from a model rather than the store.
    #: Carried so a caller can mark them (§5.4) — a computed number that
    #: rendered like a reported one would misstate where it came from.
    model_derived: tuple[str, ...] = ()
    #: Provenance per field, so a TQL result is as accountable as a screen
    #: (I1). A row that could not say where its numbers came from would be
    #: a hole in the one guarantee this system makes everywhere else.
    provenance: tuple[tuple[str, str | None], ...]


class Result(BaseModel):
    model_config = ConfigDict(frozen=True)

    plan: Plan
    rows: tuple[Row, ...]


def plan(query: Query, *, as_of: datetime) -> Plan:
    """Compile a parsed query into an execution plan.

    Every relative date resolves here, once, against a single `as_of` — so
    the fields in one query cannot disagree about what "now" means.
    """
    if query.selector.arguments:
        # `members('SPX Index')` names a universe rather than filtering one.
        # Checked first so the message says what is actually missing.
        raise TqlExecutionError(
            f"{query.selector.name}(...) names a universe, which needs index "
            "membership data that has not been ingested"
        )
    prefix = _UNIVERSES.get(query.selector.name)
    if prefix is None:
        known = ", ".join(sorted(_UNIVERSES))
        raise TqlExecutionError(
            f"universe {query.selector.name!r} is not selectable yet; known: {known}. "
            "Returning no rows would look like an empty universe rather than an "
            "unbuilt one."
        )
    window = query.dates.resolve(as_of.date()) if query.dates else None
    return Plan(
        subject_prefix=prefix,
        predicates=query.selector.predicates,
        fields=tuple(
            FieldRequest(name=f.name, mnemonic=f.mnemonic, overrides=f.overrides)
            for f in query.fields
        ),
        as_of=as_of,
        window=window,
    )


def _latest(facts: list[Fact]) -> Fact | None:
    return max(facts, key=lambda f: f.effective_from) if facts else None


def _matches(value: object, comparison: Comparison, target: Value) -> bool:
    """Compare a stored value against a predicate's target.

    Returns False rather than raising when the two are not comparable: a
    filer whose stored value is a string cannot satisfy `> 250e6`, and that
    is an exclusion, not an error in the query.
    """
    if isinstance(value, str) or isinstance(target, str):
        left, right = str(value), str(target)
    elif isinstance(value, int | float) and isinstance(target, int | float):
        left, right = value, target  # type: ignore[assignment]
    else:
        return False
    match comparison:
        case Comparison.EQ:
            return bool(left == right)
        case Comparison.NE:
            return bool(left != right)
        case Comparison.GT:
            return bool(left > right)
        case Comparison.GE:
            return bool(left >= right)
        case Comparison.LT:
            return bool(left < right)
        case Comparison.LE:
            return bool(left <= right)


def execute(plan: Plan, source: FactSource, models: ModelSource | None = None) -> Result:
    """Run a plan against a fact source.

    Predicates filter on *stored* attribute names, which are the source's
    own vocabulary — the same as-reported names DES and FA bind to, so a
    predicate means the same thing in a query as on a screen.
    """
    rows: list[Row] = []
    for subject in source.subjects_with_prefix(plan.subject_prefix, as_of=plan.as_of):
        if not _satisfies(subject, plan, source):
            continue
        values: list[tuple[str, object | None]] = []
        provenance: list[tuple[str, str | None]] = []
        computed: list[str] = []
        for request in plan.fields:
            # Models first. A model-derived field must never be served from
            # a stored value that happens to share its name — the point of
            # an override is that the number is recomputed under the stated
            # assumption, not looked up.
            result = (
                models.compute(subject, request.mnemonic, request.overrides, as_of=plan.as_of)
                if models is not None
                else None
            )
            if result is not None:
                value, provenance_id = result
                computed.append(request.name)
                values.append((request.name, value))
                provenance.append((request.name, provenance_id))
                continue

            fact = None
            for candidate in request.candidates:
                fact = _latest(source.read(subject, candidate, as_of=plan.as_of))
                if fact is not None:
                    break
            values.append((request.name, fact.value if fact else None))
            provenance.append((request.name, fact.provenance_id if fact else None))
        rows.append(
            Row(
                subject=str(subject),
                values=tuple(values),
                provenance=tuple(provenance),
                model_derived=tuple(computed),
            )
        )
    return Result(plan=plan, rows=tuple(rows))


def _satisfies(subject: TUID, plan: Plan, source: FactSource) -> bool:
    for predicate in plan.predicates:
        fact = _latest(source.read(subject, predicate.attribute, as_of=plan.as_of))
        if fact is None:
            # A subject with no value for a filtered attribute is excluded.
            # Including it would let a predicate silently widen the universe
            # instead of narrowing it.
            return False
        if not _matches(fact.value, predicate.comparison, predicate.value):
            return False
    return True
