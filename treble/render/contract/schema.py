"""Screen definition schema — invariant I6 (spec §6.1, CLAUDE.md §4).

One declarative definition drives every renderer. Definitions are authored
as ``.screen.yaml`` and validated into these models. Attributes are semantic
tokens (§6.3); themes map tokens to colours per renderer, so definitions
never mention RGB. Conditional attributes are a closed predicate set — no
expression language, no eval.
"""

from __future__ import annotations

import enum
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Attr(enum.Enum):
    """Semantic display tokens (spec §5.4, §6.3)."""

    LABEL = "label"  # amber: static labels, headers
    EDITABLE = "editable"  # white: input fields
    LINK = "link"  # cyan: drillable
    POSITIVE = "positive"  # green
    NEGATIVE = "negative"  # red
    WARNING = "warning"  # yellow
    STALE = "stale"  # grey, mandatory for non-current data
    MODEL_DERIVED = "model_derived"  # dotted underline: expandable via SPTR
    BLINK = "blink"  # updated within the last tick interval
    EMPHASIS = "emphasis"  # bright/bold


class Predicate(enum.Enum):
    """Closed conditional-attribute predicates evaluated against a bound value."""

    NEGATIVE = "negative"
    POSITIVE = "positive"
    ZERO = "zero"
    IS_STALE = "is_stale"
    IS_MODEL_DERIVED = "is_model_derived"
    IS_NULL = "is_null"


class ConditionalAttr(BaseModel):
    model_config = ConfigDict(frozen=True)
    when: Predicate
    attrs: tuple[Attr, ...]


class Pos(BaseModel):
    """Cell-grid position, zero-based, row-major."""

    model_config = ConfigDict(frozen=True)
    row: int = Field(ge=0)
    col: int = Field(ge=0)


class Rect(BaseModel):
    model_config = ConfigDict(frozen=True)
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    height: int = Field(gt=0)
    width: int = Field(gt=0)


class StaticCell(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["static"] = "static"
    at: Pos
    text: str
    attrs: tuple[Attr, ...] = (Attr.LABEL,)


class BoundCell(BaseModel):
    """A cell bound to a field-dictionary mnemonic, with optional overrides."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["bound"] = "bound"
    at: Pos
    field: str  # field dictionary mnemonic (§9.6)
    overrides: dict[str, str] = Field(default_factory=dict)
    format: str = "{}"  # str.format spec applied to the resolved value
    width: int = Field(gt=0, default=12)
    attrs: tuple[Attr, ...] = ()
    conditional: tuple[ConditionalAttr, ...] = ()


class InputCell(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["input"] = "input"
    at: Pos
    name: str
    input_type: Literal["text", "number", "date"] = "text"
    width: int = Field(gt=0, default=12)
    default: str | None = None


class LinkCell(BaseModel):
    """A drillable cell: <GO> executes the command template (spec §5.4)."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["link"] = "link"
    at: Pos
    text: str
    command: str  # e.g. "{security} DES", "{issuer_ticker} Equity RELS"
    attrs: tuple[Attr, ...] = (Attr.LINK,)


class PaneType(enum.Enum):
    TIMESERIES = "timeseries"  # TUI sparkline / desktop chart
    HEATMAP = "heatmap"
    SURFACE = "surface"
    TABLE_SCROLL = "table_scroll"


class PaneRegion(BaseModel):
    """A rectangle delegated to a graphical renderer. Conformance asserts
    region, type, and data binding — never pixels (CLAUDE.md §4)."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["pane"] = "pane"
    region: Rect
    pane_type: PaneType
    binding: str  # field mnemonic or TQL expression supplying the data


Cell = Annotated[
    StaticCell | BoundCell | InputCell | LinkCell | PaneRegion,
    Field(discriminator="kind"),
]


class TabDef(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    cells: tuple[Cell, ...]


class ScreenDef(BaseModel):
    """One named function's declarative screen (spec §7)."""

    model_config = ConfigDict(frozen=True)

    mnemonic: str  # from spec §24 — never invented
    title: str
    rows: int = Field(gt=0)
    cols: int = Field(gt=0)
    namespaces: tuple[str, ...] = ()  # applicable yellow keys; empty = global function
    tabs: tuple[TabDef, ...]

    @model_validator(mode="after")
    def _cells_inside_grid(self) -> ScreenDef:
        for tab in self.tabs:
            for cell in tab.cells:
                if isinstance(cell, PaneRegion):
                    r = cell.region
                    if r.row + r.height > self.rows or r.col + r.width > self.cols:
                        raise ValueError(f"{self.mnemonic}/{tab.name}: pane exceeds grid bounds")
                elif cell.at.row >= self.rows or cell.at.col >= self.cols:
                    raise ValueError(
                        f"{self.mnemonic}/{tab.name}: cell at "
                        f"({cell.at.row},{cell.at.col}) outside {self.rows}x{self.cols}"
                    )
        return self


def load_screen(text: str) -> ScreenDef:
    """Parse and validate a .screen.yaml document."""
    return ScreenDef.model_validate(yaml.safe_load(text))
