"""Resolver contract (CLAUDE.md §4): resolve(definition, context, as_of) -> CellBuffer.

Resolvers call TAPI and never storage (I7). The TAPI surface a resolver may
touch is expressed here as a Protocol so resolvers are testable against a
frozen fake — which is exactly what the conformance suite does.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from treble.core.identifiers import SecurityQuery
from treble.core.provenance import ProvenanceId
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
    Predicate,
    ScreenDef,
    StaticCell,
)


class FieldResult(BaseModel):
    """One resolved field value as TAPI returns it to presentation code."""

    model_config = ConfigDict(frozen=True)

    value: str | float | int | bool | None
    provenance_id: ProvenanceId | None = None
    stale: bool = False
    model_derived: bool = False


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

    for cell in tab.cells:
        if isinstance(cell, StaticCell):
            cells.append(
                ResolvedCell(row=cell.at.row, col=cell.at.col, text=cell.text, attrs=cell.attrs)
            )
        elif isinstance(cell, BoundCell):
            result = tapi.field(context.security, cell.field, cell.overrides, as_of=as_of)
            any_stale = any_stale or result.stale
            attrs = tuple(
                dict.fromkeys((*cell.attrs, *_conditional_attrs(result, cell.conditional)))
            )
            text = "—" if result.value is None else cell.format.format(result.value)
            cells.append(
                ResolvedCell(
                    row=cell.at.row,
                    col=cell.at.col,
                    text=text[: cell.width].ljust(0),
                    attrs=attrs,
                    provenance_id=result.provenance_id,
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
            panes.append(
                ResolvedPane(
                    region=cell.region,
                    pane_type=cell.pane_type,
                    binding=cell.binding,
                    data=data,
                )
            )

    return CellBuffer(
        mnemonic=definition.mnemonic,
        tab=tab.name,
        rows=definition.rows,
        cols=definition.cols,
        cells=tuple(cells),
        panes=tuple(panes),
        stale=any_stale,
    )
