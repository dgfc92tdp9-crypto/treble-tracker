"""TUI renderer — CellBuffer to styled terminal output (spec §4, §6.1).

The renderer's only input is a `CellBuffer`. It knows nothing about
storage, analytics or where values came from, which is what makes the same
screen definition drive this and the desktop client (I6).

**Conformance.** This renderer must reproduce the same abstract layout tree
and text snapshot as every other renderer, from the same definition and the
same frozen TAPI responses (CLAUDE.md §4). It is registered with the
conformance suite so that is checked rather than assumed.

Graphical panes are the one legitimate difference (§6.1): where the desktop
client draws a WebGL chart, the TUI draws a braille sparkline. The suite
asserts the pane's region, type and data binding — never its pixels.
"""

from __future__ import annotations

from rich.text import Text

from treble.render.contract.buffer import (
    CellBuffer,
    ResolvedPane,
    layout_tree,
    pane_placeholder,
)
from treble.render.contract.schema import PaneType
from treble.render.tui.theme import DEFAULT_THEME, Theme

#: Braille dot rows, low to high. Eight levels in a single character cell,
#: which is what makes a text sparkline legible at terminal density.
_BRAILLE = "⣀⣄⣤⣦⣶⣷⣿"


def sparkline(values: list[float], width: int) -> str:
    """A braille sparkline — the TUI's answer to a chart pane.

    Returns an empty string for fewer than two points rather than drawing a
    flat line, which would imply data that is not there.
    """
    points = [v for v in values if v is not None]
    if len(points) < 2 or width < 1:
        return ""
    lo, hi = min(points), max(points)
    span = hi - lo
    # Resample to the available width.
    step = max(1, len(points) // width)
    sampled = points[::step][:width]
    if span == 0:
        return _BRAILLE[len(_BRAILLE) // 2] * len(sampled)
    return "".join(
        _BRAILLE[min(len(_BRAILLE) - 1, int((v - lo) / span * (len(_BRAILLE) - 1)))]
        for v in sampled
    )


def render_pane(pane: ResolvedPane) -> list[str]:
    """A pane as text lines. Region and binding are honoured exactly; the
    *drawing* is the TUI's own (§6.1)."""
    height, width = pane.region.height, pane.region.width
    lines: list[str] = []
    if pane.pane_type is PaneType.TIMESERIES and pane.data:
        numeric = [float(row[-1]) for row in pane.data if row and isinstance(row[-1], int | float)]
        spark = sparkline(numeric, max(width - 2, 1))
        lines.append(f"[{pane.pane_type.value}:{pane.binding}]"[:width])
        if spark:
            lines.append(spark)
    else:
        lines.append(f"[{pane.pane_type.value}:{pane.binding}]"[:width])
    while len(lines) < height:
        lines.append("")
    return lines[:height]


def render_text(buffer: CellBuffer) -> Text:
    """The buffer as styled Rich text, one line per grid row."""
    return render_styled(buffer, DEFAULT_THEME)


def render_styled(buffer: CellBuffer, theme: Theme, *, draw_panes: bool = True) -> Text:
    """Render with an explicit theme (§6.3: semantics survive the palette).

    ``draw_panes=False`` substitutes the renderer-neutral pane shape used
    for conformance; display always draws the real sparkline.
    """
    grid: list[list[str]] = [[" "] * buffer.cols for _ in range(buffer.rows)]
    styles: list[list[str]] = [[""] * buffer.cols for _ in range(buffer.rows)]

    def place(row: int, col: int, text: str, style: str) -> None:
        for offset, char in enumerate(text):
            if 0 <= row < buffer.rows and 0 <= col + offset < buffer.cols:
                grid[row][col + offset] = char
                styles[row][col + offset] = style

    for cell in sorted(buffer.cells, key=lambda c: (c.row, c.col)):
        place(cell.row, cell.col, cell.text, theme.style_for(cell.attrs))

    for pane in buffer.panes:
        lines = render_pane(pane) if draw_panes else pane_placeholder(pane)
        for offset, line in enumerate(lines):
            place(pane.region.row + offset, pane.region.col, line, "")

    out = Text()
    for row_index in range(buffer.rows):
        col_index = 0
        while col_index < buffer.cols:
            style = styles[row_index][col_index]
            run = grid[row_index][col_index]
            col_index += 1
            while col_index < buffer.cols and styles[row_index][col_index] == style:
                run += grid[row_index][col_index]
                col_index += 1
            out.append(run.rstrip() if col_index >= buffer.cols else run, style=style or None)
        if row_index < buffer.rows - 1:
            out.append("\n")
    return out


def conformance_artifacts(buffer: CellBuffer) -> tuple[str, str]:
    """(layout tree, text snapshot) as the conformance suite requires.

    The text snapshot is taken from the *styled* render with styling
    stripped, so this proves the TUI's own pipeline produces the agreed
    output — not that a second code path happens to agree.
    """
    styled = render_styled(buffer, DEFAULT_THEME, draw_panes=False)
    text = "\n".join(line.rstrip() for line in styled.plain.split("\n"))
    return layout_tree(buffer), text.rstrip("\n") + "\n"
