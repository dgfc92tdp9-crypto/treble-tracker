"""CellBuffer — the resolved form of a screen (CLAUDE.md §4).

``resolve(definition, context, as_of) -> CellBuffer`` produces this. It is
renderer-independent: every renderer consumes a CellBuffer and nothing else.
Two canonical projections back the conformance suite (I6):

- :func:`layout_tree` — deterministic JSON of everything a renderer must show
- :func:`text_snapshot` — plain-character render of the grid

Renderers that disagree with either projection fail conformance.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from treble.core.provenance import ProvenanceId
from treble.render.contract.schema import Attr, PaneType, Rect


class ResolvedCell(BaseModel):
    model_config = ConfigDict(frozen=True)

    row: int
    col: int
    text: str
    attrs: tuple[Attr, ...] = ()
    link_command: str | None = None
    provenance_id: ProvenanceId | None = None  # hover/SPTR target (I1)
    input_name: str | None = None


class ResolvedPane(BaseModel):
    model_config = ConfigDict(frozen=True)

    region: Rect
    pane_type: PaneType
    binding: str
    # The bound series/table data, resolved through TAPI. Kept as primitive
    # rows so the conformance suite can assert the binding's data without
    # caring how a renderer draws it.
    data: tuple[tuple[str | float | int | None, ...], ...] = ()


class CellBuffer(BaseModel):
    model_config = ConfigDict(frozen=True)

    mnemonic: str
    tab: str
    rows: int
    cols: int
    cells: tuple[ResolvedCell, ...]
    panes: tuple[ResolvedPane, ...] = ()
    stale: bool = False  # whole-screen staleness banner (§6.3)
    footnotes: tuple[str, ...] = Field(default_factory=tuple)


def layout_tree(buffer: CellBuffer) -> str:
    """Canonical JSON projection: sorted cells, stable key order, no floats drift."""
    payload = {
        "mnemonic": buffer.mnemonic,
        "tab": buffer.tab,
        "grid": [buffer.rows, buffer.cols],
        "stale": buffer.stale,
        "cells": [
            {
                "at": [c.row, c.col],
                "text": c.text,
                "attrs": sorted(a.value for a in c.attrs),
                "link": c.link_command,
                "provenance": c.provenance_id,
                "input": c.input_name,
            }
            for c in sorted(buffer.cells, key=lambda c: (c.row, c.col))
        ],
        "panes": [
            {
                "region": [p.region.row, p.region.col, p.region.height, p.region.width],
                "type": p.pane_type.value,
                "binding": p.binding,
                "data": [list(row) for row in p.data],
            }
            for p in sorted(buffer.panes, key=lambda p: (p.region.row, p.region.col))
        ],
        "footnotes": list(buffer.footnotes),
    }
    return canonical_json(payload)


def canonical_json(payload: object) -> str:
    """The one serialisation used for layout goldens.

    Exists so no caller can reproduce these parameters by hand and drift
    from them — which is precisely what happened when the web renderer was
    wired in and re-dumped with ``ensure_ascii`` left at its default.
    """
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


def pane_placeholder(pane: ResolvedPane) -> list[str]:
    """A pane drawn renderer-neutrally: border, type and binding only.

    Conformance asserts a pane's region, type and data binding — never its
    pixels (CLAUDE.md §4), because the TUI legitimately draws a sparkline
    where the desktop draws a WebGL chart. Every renderer uses this shape
    for its conformance artefacts and draws whatever it likes for display.
    """
    r = pane.region
    horizontal = "+" + "-" * (r.width - 2) + "+" if r.width >= 2 else "+"
    lines = [horizontal]
    label = f"[{pane.pane_type.value}:{pane.binding}]"[: max(r.width - 2, 0)]
    for index in range(1, r.height - 1):
        body = label if index == 1 else ""
        lines.append("|" + body.ljust(max(r.width - 2, 0)) + "|")
    if r.height > 1:
        lines.append(horizontal)
    return lines[: r.height]


def text_snapshot(buffer: CellBuffer) -> str:
    """Character-grid projection. Pane regions render as a bordered box naming
    the pane type and binding — pixels are renderer-specific, regions are not."""
    grid = [[" "] * buffer.cols for _ in range(buffer.rows)]

    def write(row: int, col: int, text: str) -> None:
        for i, ch in enumerate(text):
            if col + i < buffer.cols:
                grid[row][col + i] = ch

    for cell in sorted(buffer.cells, key=lambda c: (c.row, c.col)):
        write(cell.row, cell.col, cell.text)

    for pane in buffer.panes:
        r = pane.region
        horizontal = "+" + "-" * (r.width - 2) + "+" if r.width >= 2 else "+"
        write(r.row, r.col, horizontal)
        write(r.row + r.height - 1, r.col, horizontal)
        for middle_row in range(r.row + 1, r.row + r.height - 1):
            write(middle_row, r.col, "|")
            write(middle_row, r.col + r.width - 1, "|")
        label = f"[{pane.pane_type.value}:{pane.binding}]"[: max(r.width - 2, 0)]
        if r.height > 1:
            write(r.row + 1, r.col + 1, label)

    lines = ["".join(row).rstrip() for row in grid]
    return "\n".join(lines).rstrip("\n") + "\n"
