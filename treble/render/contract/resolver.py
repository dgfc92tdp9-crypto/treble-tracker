"""Resolver contract (CLAUDE.md §4): resolve(definition, context, as_of) -> CellBuffer.

Resolvers call TAPI and never storage (I7). The TAPI surface a resolver may
touch is expressed here as a Protocol so resolvers are testable against a
frozen fake — which is exactly what the conformance suite does.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from treble.core.consistency import check as check_statement
from treble.core.identifiers import SecurityQuery
from treble.render.contract.buffer import (
    CellBuffer,
    ResolvedCell,
    ResolvedPane,
)
from treble.render.contract.schema import (
    Attr,
    BoundCell,
    ConditionalAttr,
    InputCell,
    LinkCell,
    PaneRegion,
    PeriodCell,
    Predicate,
    ScreenDef,
    StaticCell,
)
from treble.tapi.types import FieldResult


class TapiView(Protocol):
    """The slice of TAPI available to resolvers (I7: the only data path)."""

    def field(
        self,
        security: SecurityQuery | None,
        mnemonic: str,
        overrides: dict[str, str],
        *,
        as_of: datetime,
    ) -> FieldResult: ...

    def series(
        self, security: SecurityQuery | None, binding: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]: ...


class ScreenContext(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    security: SecurityQuery | None = None
    tab: str | None = None  # None = first tab
    inputs: dict[str, str] = Field(default_factory=dict)


#: The em dash a missing value renders as (§6.3: never a zero, never blank).
MISSING = "—"


def _missing(spec: str, width: int) -> str:
    """The missing-value mark, occupying the column the value would have.

    A bare ``"—"`` is one character written at the cell's origin, while the
    number it stands in for is right-aligned across the cell's width. In a
    right-aligned money column that puts the dash roughly twenty columns to
    the left of every figure it lines up with — and where the row's label
    runs past the cell origin, *on top of the label*:

        Cash and equivalents, carrying val—e

    which is what the `fa_cashflow` golden rendered the moment a null
    appeared in it. Pre-existing, and invisible until then only because
    every cell in that column had a value and the screens that did show
    nulls had labels short enough not to collide.
    """
    if ">" in spec:
        return MISSING.rjust(width)
    if "^" in spec:
        return MISSING.center(width)
    return MISSING


def _same_period(value: FieldResult, governing: FieldResult) -> bool:
    """Whether ``value`` belongs under a heading stating ``governing``'s period.

    Not plain equality, because XBRL carries two shapes and a cash flow
    statement legitimately mixes them: **durations** (`2026-01-01` to
    `2026-03-31`, the flows) and **instants** (`2026-03-31`, the closing
    balance that ends them). Requiring the tuples to match would blank every
    balance line under a flow heading — correct output turned into an em
    dash, which is its own kind of wrong.

    So an instant agrees with a duration when it falls on that duration's
    **end**: the closing balance of the period the heading names. An instant
    three months later — which is what the `fa_cashflow` fixture held, a 30
    June cash balance under a "3 months to 31 March" heading — does not.
    """
    if value.effective_from is None or governing.effective_to is None:
        return False
    if value.effective_from == value.effective_to:
        return value.effective_from == governing.effective_to
    return (value.effective_from, value.effective_to) == (
        governing.effective_from,
        governing.effective_to,
    )


def _conditional_attrs(
    result: FieldResult, conditions: tuple[ConditionalAttr, ...]
) -> tuple[Attr, ...]:
    extra: list[Attr] = []
    value = result.value
    for cond in conditions:
        matched = (
            (cond.when == Predicate.NEGATIVE and isinstance(value, int | float) and value < 0)
            or (cond.when == Predicate.POSITIVE and isinstance(value, int | float) and value > 0)
            or (cond.when == Predicate.ZERO and isinstance(value, int | float) and value == 0)
            or (cond.when == Predicate.IS_STALE and result.stale)
            or (cond.when == Predicate.IS_MODEL_DERIVED and result.model_derived)
            or (cond.when == Predicate.IS_NULL and value is None)
        )
        if matched:
            extra.extend(cond.attrs)
    return tuple(extra)


def resolve(
    definition: ScreenDef,
    context: ScreenContext,
    *,
    as_of: datetime,
    tapi: TapiView,
) -> CellBuffer:
    """The generic resolver: walk the definition, bind fields through TAPI.

    Screens with behaviour beyond declarative binding provide their own
    resolver function with this same signature; most screens need only this.
    """
    tab = next(
        (t for t in definition.tabs if context.tab is None or t.name == context.tab),
        definition.tabs[0],
    )
    cells: list[ResolvedCell] = []
    panes: list[ResolvedPane] = []
    any_stale = False
    # Field values as assembled for *this* screen, checked against the
    # accounting identities once every cell is resolved. `period_from`
    # answers "did this number come from the period the heading claims";
    # this answers the question it cannot — whether the numbers on the
    # screen agree with each other (spec §14.1).
    statement: dict[str, float] = {}
    #: Index in `cells` -> the field it is bound to, so a failing identity
    #: can mark exactly the cells that produced it.
    bound_at: dict[int, str] = {}

    for cell in tab.cells:
        if isinstance(cell, StaticCell):
            cells.append(
                ResolvedCell(row=cell.at.row, col=cell.at.col, text=cell.text, attrs=cell.attrs)
            )
        elif isinstance(cell, BoundCell):
            result = tapi.field(context.security, cell.field, cell.overrides, as_of=as_of)
            if cell.period_from is not None:
                governing = tapi.field(
                    context.security, cell.period_from, cell.overrides, as_of=as_of
                )
                if not _same_period(result, governing):
                    # A value from a different period than the heading above
                    # it. Blank, not shown: see `BoundCell.period_from`.
                    result = FieldResult(value=None)
            any_stale = any_stale or result.stale
            attrs = tuple(
                dict.fromkeys((*cell.attrs, *_conditional_attrs(result, cell.conditional)))
            )
            if isinstance(result.value, int | float) and not isinstance(result.value, bool):
                statement[cell.field] = float(result.value)
            bound_at[len(cells)] = cell.field
            text = (
                _missing(cell.format, cell.width)
                if result.value is None
                else cell.format.format(result.value)
            )
            cells.append(
                ResolvedCell(
                    row=cell.at.row,
                    col=cell.at.col,
                    text=text[: cell.width].ljust(0),
                    attrs=attrs,
                    provenance_id=result.provenance_id,
                )
            )
        elif isinstance(cell, PeriodCell):
            result = tapi.field(context.security, cell.field, cell.overrides, as_of=as_of)
            label = result.period_label
            cells.append(
                ResolvedCell(
                    row=cell.at.row,
                    col=cell.at.col,
                    # An unknown period says so rather than rendering blank:
                    # a missing label would read as "no period qualifier
                    # needed", which is the ambiguity this cell exists to fix.
                    text=(label or "period not stated")[: cell.width],
                    attrs=cell.attrs,
                )
            )
        elif isinstance(cell, InputCell):
            value = context.inputs.get(cell.name, cell.default or "")
            cells.append(
                ResolvedCell(
                    row=cell.at.row,
                    col=cell.at.col,
                    text=value.ljust(cell.width)[: cell.width],
                    attrs=(Attr.EDITABLE,),
                    input_name=cell.name,
                )
            )
        elif isinstance(cell, LinkCell):
            command = cell.command
            if context.security is not None:
                command = command.replace("{security}", context.security.display())
            cells.append(
                ResolvedCell(
                    row=cell.at.row,
                    col=cell.at.col,
                    text=cell.text,
                    attrs=cell.attrs,
                    link_command=command,
                )
            )
        elif isinstance(cell, PaneRegion):
            data = tapi.series(context.security, cell.binding, as_of=as_of)
            if cell.order == "desc":
                data = tuple(reversed(data))
            panes.append(
                ResolvedPane(
                    region=cell.region,
                    pane_type=cell.pane_type,
                    binding=cell.binding,
                    data=data,
                )
            )

    # Spec §14.1: a statement that does not foot is not presented as though
    # it does. Reported as a footnote rather than by blanking the figures —
    # every one of them may be exactly what the filer said, and which is
    # wrong is not something this layer can know.
    violations = check_statement(statement)
    footnotes = tuple(
        f"These figures do not reconcile — {violation}. Every value is as "
        "filed; the disagreement is between them."
        for violation in violations
    )
    if violations:
        flagged = {field for violation in violations for field in violation.fields}
        cells = [
            cell.model_copy(update={"attrs": (*cell.attrs, Attr.WARNING)})
            if bound_at.get(index) in flagged and Attr.WARNING not in cell.attrs
            else cell
            for index, cell in enumerate(cells)
        ]

    return CellBuffer(
        mnemonic=definition.mnemonic,
        tab=tab.name,
        rows=definition.rows,
        cols=definition.cols,
        cells=tuple(cells),
        panes=tuple(panes),
        stale=any_stale,
        footnotes=footnotes,
    )
