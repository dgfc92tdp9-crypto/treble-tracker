"""TQL — the Treble Query Language (spec §4.2).

    get(px_last, oas_spread_mid(vol_override=0.20), dur_adj_oas)
    for(bonds(issuer_ticker='IBM', currency='USD', amt_outstanding > 250e6))
    with(dates=range(-1Y, 0D), fill='prev')

Three clauses: what to retrieve, over which universe, under what options.
This module is the front half — text to a validated syntax tree. Planning
and execution are separate so the language can be checked without a store,
and so a query can be shown to a user before it runs.

**Parsing is total and typed.** A malformed query raises `TqlSyntaxError`
with the position, never a partial tree: a query that half-parsed would
select a universe nobody asked for, and in a system whose whole point is
that displayed numbers are accountable, a silently narrowed universe is a
wrong answer with no visible symptom.

Overrides are first-class here rather than an afterthought. The spec calls
them "the mechanism by which the entire analytics library is exposed as
data" (§4.2) — `oas_spread_mid(vol_override=0.20)` is not a different field
from `oas_spread_mid`, it is the same field under a stated assumption, and
the assumption travels with the request into the I3 envelope.
"""

from __future__ import annotations

import enum
from datetime import date, timedelta
from typing import Literal

from lark import Lark, LarkError, Token, Transformer
from pydantic import BaseModel, ConfigDict

GRAMMAR = r"""
    ?start: query

    query: clause+

    ?clause: get_clause | for_clause | with_clause

    get_clause:  "get"i  "(" [field ("," field)*] ")"
    for_clause:  "for"i  "(" selector ")"
    with_clause: "with"i "(" [option ("," option)*] ")"

    field: FIELD_NAME ["(" [override ("," override)*] ")"]
    override: NAME "=" value

    // A universe takes predicates, positional arguments, or both:
    // bonds(currency='USD') and members('SPX Index') are both selectors.
    selector: NAME "(" [argument ("," argument)*] ")"
    ?argument: predicate | value
    predicate: FIELD_NAME COMPARISON value

    option: NAME "=" value

    ?value: range | STRING | TENOR | SIGNED_NUMBER | NAME

    range: "range"i "(" value "," value ")"

    COMPARISON: ">=" | "<=" | "!=" | ">" | "<" | "="
    // Higher priority than SIGNED_NUMBER, which would otherwise take the
    // "-1" of "-1Y" and leave a stray "Y".
    TENOR.2: /[+-]?\d+[DWMY]/
    NAME: /[A-Za-z_][A-Za-z0-9_]*/
    // As-reported source tags are the system's primary vocabulary and are
    // not identifiers: `us-gaap:Assets:USD` carries hyphens, colons and a
    // unit that may contain a slash. Higher priority than NAME so a
    // qualified tag is one token rather than three and a syntax error.
    FIELD_NAME.3: /[A-Za-z_][A-Za-z0-9_-]*(:[A-Za-z0-9_.\/-]+)+|[A-Za-z_][A-Za-z0-9_]*/
    STRING: /'[^']*'/ | /"[^"]*"/

    %import common.SIGNED_NUMBER
    %import common.WS
    %ignore WS
"""


class TqlSyntaxError(ValueError):
    """The query is not valid TQL. Carries the position, never a tree."""


class Comparison(enum.Enum):
    EQ = "="
    NE = "!="
    GT = ">"
    GE = ">="
    LT = "<"
    LE = "<="


class Tenor(BaseModel):
    """A relative date such as ``-1Y`` or ``0D``.

    Kept symbolic rather than resolved at parse time: a query is a
    description, and resolving ``-1Y`` against the parse-time clock would
    make the same query mean different things on different days — which is
    precisely what I2 exists to prevent. It resolves against the request's
    ``as_of``, at execution.
    """

    model_config = ConfigDict(frozen=True)

    amount: int
    unit: Literal["D", "W", "M", "Y"]

    @classmethod
    def parse(cls, text: str) -> Tenor:
        return cls(amount=int(text[:-1]), unit=text[-1])

    def resolve(self, as_of: date) -> date:
        """The date this tenor names, relative to ``as_of``."""
        match self.unit:
            case "D":
                return as_of + timedelta(days=self.amount)
            case "W":
                return as_of + timedelta(weeks=self.amount)
            case "M":
                months = as_of.month - 1 + self.amount
                year, month = as_of.year + months // 12, months % 12 + 1
                # Clamp: 31 March less one month is 28 February, not the 31st.
                day = min(as_of.day, _days_in_month(year, month))
                return date(year, month, day)
            case "Y":
                try:
                    return as_of.replace(year=as_of.year + self.amount)
                except ValueError:  # 29 February in a non-leap year
                    return as_of.replace(year=as_of.year + self.amount, day=28)

    def __str__(self) -> str:
        return f"{self.amount}{self.unit}"


def _days_in_month(year: int, month: int) -> int:
    nxt = date(year + month // 12, month % 12 + 1, 1)
    return (nxt - date(year, month, 1)).days


class DateRange(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: Tenor
    end: Tenor

    def resolve(self, as_of: date) -> tuple[date, date]:
        return self.start.resolve(as_of), self.end.resolve(as_of)


Value = str | float | int | Tenor | DateRange


class Field(BaseModel):
    """A requested field, with the overrides it is to be computed under."""

    model_config = ConfigDict(frozen=True)

    name: str
    overrides: tuple[tuple[str, Value], ...] = ()

    @property
    def mnemonic(self) -> str:
        """The field-dictionary name.

        TQL is written lower-case by convention and documented mnemonics are
        upper-case (§9.6). An as-reported tag is left exactly as written:
        `us-gaap:Assets:USD` is the filer's own name for the thing, and
        upper-casing it would name a tag that does not exist.
        """
        return self.name if ":" in self.name else self.name.upper()


class Predicate(BaseModel):
    model_config = ConfigDict(frozen=True)

    attribute: str
    comparison: Comparison
    value: Value


class Selector(BaseModel):
    """A universe: a named function over predicates, e.g.
    ``bonds(currency='USD')`` or ``members('SPX Index')``."""

    model_config = ConfigDict(frozen=True)

    name: str
    predicates: tuple[Predicate, ...] = ()
    #: Positional arguments, as in ``members('SPX Index')``. Kept separate
    #: from predicates because they select a universe by naming it rather
    #: than by filtering attributes, and a planner resolves the two through
    #: different paths.
    arguments: tuple[Value, ...] = ()


class Query(BaseModel):
    model_config = ConfigDict(frozen=True)

    fields: tuple[Field, ...]
    selector: Selector
    options: tuple[tuple[str, Value], ...] = ()

    def option(self, name: str) -> Value | None:
        for key, value in self.options:
            if key == name:
                return value
        return None

    @property
    def dates(self) -> DateRange | None:
        found = self.option("dates")
        return found if isinstance(found, DateRange) else None


class _Builder(Transformer):  # type: ignore[type-arg]
    def STRING(self, token: Token) -> str:  # noqa: N802
        return str(token)[1:-1]

    def TENOR(self, token: Token) -> Tenor:  # noqa: N802
        return Tenor.parse(str(token))

    def SIGNED_NUMBER(self, token: Token) -> float | int:  # noqa: N802
        text = str(token)
        # 250e6 is a count of bonds outstanding, not 250000000.0 rendered in
        # scientific notation; integral values stay integral.
        number = float(text)
        return int(number) if number.is_integer() and "." not in text.lower() else number

    def NAME(self, token: Token) -> str:  # noqa: N802
        return str(token)

    def FIELD_NAME(self, token: Token) -> str:  # noqa: N802
        return str(token)

    def COMPARISON(self, token: Token) -> Comparison:  # noqa: N802
        return Comparison(str(token))

    def range(self, children: list[Value]) -> DateRange:
        start, end = children
        if not isinstance(start, Tenor) or not isinstance(end, Tenor):
            raise TqlSyntaxError("range() takes two tenors, e.g. range(-1Y, 0D)")
        return DateRange(start=start, end=end)

    def override(self, children: list[object]) -> tuple[str, Value]:
        return str(children[0]), children[1]  # type: ignore[return-value]

    option = override

    def field(self, children: list[object]) -> Field:
        name, *overrides = children
        return Field(
            name=str(name),
            overrides=tuple(o for o in overrides if isinstance(o, tuple)),
        )

    def predicate(self, children: list[object]) -> Predicate:
        attribute, comparison, value = children
        return Predicate(
            attribute=str(attribute),
            comparison=comparison,
            value=value,
        )

    def selector(self, children: list[object]) -> Selector:
        name, *rest = children
        return Selector(
            name=str(name),
            predicates=tuple(p for p in rest if isinstance(p, Predicate)),
            arguments=tuple(a for a in rest if a is not None and not isinstance(a, Predicate)),
        )

    def get_clause(self, children: list[object]) -> tuple[str, object]:
        return "get", tuple(c for c in children if isinstance(c, Field))

    def for_clause(self, children: list[object]) -> tuple[str, object]:
        return "for", children[0]

    def with_clause(self, children: list[object]) -> tuple[str, object]:
        return "with", tuple(c for c in children if isinstance(c, tuple))

    def query(self, children: list[tuple[str, object]]) -> Query:
        clauses = dict(children)
        fields = clauses.get("get")
        selector = clauses.get("for")
        if not fields:
            raise TqlSyntaxError("a query must retrieve something: get(...) is required")
        if selector is None:
            raise TqlSyntaxError("a query must name a universe: for(...) is required")
        return Query(
            fields=fields,
            selector=selector,
            options=clauses.get("with", ()),
        )


_PARSER = Lark(GRAMMAR, parser="lalr", transformer=_Builder())


def parse_tql(text: str) -> Query:
    """Parse TQL into a validated query, or raise.

    Total: every input either yields a complete query or raises. There is no
    partial result, because a query that half-parsed would run against a
    universe the user never asked for.
    """
    try:
        result = _PARSER.parse(text)
    except TqlSyntaxError:
        raise
    except LarkError as error:
        raise TqlSyntaxError(f"invalid TQL: {error}") from error
    if not isinstance(result, Query):
        raise TqlSyntaxError("invalid TQL: not a query")
    return result
