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

from treble.render.canvas import (
    Canvas,
    canvas_layout_tree,
    canvas_text_snapshot,
    components_on_display,
    frame_lines,
    normalise_content,
)
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


def table_lines(pane: ResolvedPane, *, height: int, width: int) -> list[str]:
    """A table pane drawn as aligned columns.

    Needed because MDL, FLDS and SPTR are tables, and a pane type with no
    drawing renders as an empty box: the data resolves, conformance passes
    (it asserts region and binding, never pixels) and the user sees
    nothing. Truncation is always announced for the same reason — a table
    silently cut off is a wrong display, not a tidy one.
    """
    rows = [["" if cell is None else str(cell) for cell in row] for row in pane.data]
    if not rows:
        return [f"[{pane.binding}] no rows"[:width]]

    columns = max(len(row) for row in rows)
    widths = [max((len(row[i]) for row in rows if i < len(row)), default=0) for i in range(columns)]

    gap = 2
    # Shrink the widest column repeatedly rather than scaling everything:
    # it keeps short identifier columns intact and truncates prose.
    while sum(widths) + gap * (columns - 1) > width and max(widths) > 4:
        widths[widths.index(max(widths))] -= 1

    visible = rows
    footer = ""
    if len(rows) > height:
        visible = rows[: height - 1]
        footer = f"... {len(rows) - len(visible)} more rows"

    lines = [
        (" " * gap)
        .join(
            (row[i] if i < len(row) else "")[: widths[i]].ljust(widths[i]) for i in range(columns)
        )
        .rstrip()[:width]
        for row in visible
    ]
    if footer:
        lines.append(footer[:width])
    return lines


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
    elif pane.pane_type is PaneType.TABLE_SCROLL:
        lines.extend(table_lines(pane, height=height, width=width))
    else:
        lines.append(f"[{pane.pane_type.value}:{pane.binding}]"[:width])
    while len(lines) < height:
        lines.append("")
    return lines[:height]


def render_text(buffer: CellBuffer) -> Text:
    """The buffer as styled Rich text, one line per grid row."""
    return render_styled(buffer, DEFAULT_THEME)


def styled_grid(
    buffer: CellBuffer, theme: Theme, *, draw_panes: bool = True
) -> tuple[list[list[str]], list[list[str]]]:
    """(characters, styles) for a buffer, as parallel row-major grids.

    Split out from :func:`render_styled` so the canvas compositor can place a
    component's characters *and* its styling without a second implementation
    of either. A style grid rebuilt independently would be a second source of
    truth for what a cell looks like.
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

    return grid, styles


def _to_text(grid: list[list[str]], styles: list[list[str]]) -> Text:
    """Collapse parallel character/style grids into Rich runs."""
    out = Text()
    rows, cols = len(grid), len(grid[0]) if grid else 0
    for row_index in range(rows):
        col_index = 0
        while col_index < cols:
            style = styles[row_index][col_index]
            run = grid[row_index][col_index]
            col_index += 1
            while col_index < cols and styles[row_index][col_index] == style:
                run += grid[row_index][col_index]
                col_index += 1
            out.append(run.rstrip() if col_index >= cols else run, style=style or None)
        if row_index < rows - 1:
            out.append("\n")
    return out


def render_styled(buffer: CellBuffer, theme: Theme, *, draw_panes: bool = True) -> Text:
    """Render with an explicit theme (§6.3: semantics survive the palette).

    ``draw_panes=False`` substitutes the renderer-neutral pane shape used
    for conformance; display always draws the real sparkline.
    """
    return _to_text(*styled_grid(buffer, theme, draw_panes=draw_panes))


def render_canvas_styled(
    canvas: Canvas,
    buffers: dict[str, CellBuffer],
    theme: Theme,
    *,
    display: int = 0,
    draw_panes: bool = True,
) -> Text:
    """A whole workspace as styled terminal output (spec §5.3).

    The frame geometry comes from :func:`~treble.render.canvas.frame_lines`,
    the same function the text projection uses, so this cannot drift from it.
    What the TUI adds is the *styling*: each component's cells keep their
    semantic attributes, and the frame is drawn in its colour group so a link
    is visible on the terminal exactly as it is on the desktop.

    A terminal has one grid and no windows, so `display` selects which
    display's components to draw rather than showing all of them at once —
    overlaying two displays' placements would collide arbitrary components.
    """
    on_display = components_on_display(canvas, buffers, display)
    height = max((p.y + p.height for _, p, _ in on_display), default=0)
    width = max((p.x + p.width for _, p, _ in on_display), default=0)
    grid: list[list[str]] = [[" "] * width for _ in range(height)]
    styles: list[list[str]] = [[""] * width for _ in range(height)]

    for component, placement, buffer in on_display:
        component_grid, component_styles = styled_grid(buffer, theme, draw_panes=draw_panes)
        content = normalise_content(["".join(row) for row in component_grid])
        frame = frame_lines(component, placement, content)
        frame_style = theme.style_for_channel(
            component.channel.value if component.channel else None
        )
        for row_offset, line in enumerate(frame):
            row = placement.y + row_offset
            if not 0 <= row < height:
                continue
            for col_offset, char in enumerate(line):
                col = placement.x + col_offset
                if not 0 <= col < width:
                    continue
                grid[row][col] = char
                # Interior characters keep the component's own styling; the
                # border and title carry the colour group. `frame_lines` has
                # already clipped and padded, so the two agree on which
                # characters exist by construction rather than by luck.
                inner_row = row_offset - 1
                inner_col = col_offset - 1
                interior = (
                    0 <= inner_row < len(component_styles)
                    and 0 <= inner_col < len(component_styles[inner_row])
                    and 0 < row_offset < placement.height - 1
                    and 0 < col_offset < placement.width - 1
                )
                styles[row][col] = (
                    component_styles[inner_row][inner_col] if interior else frame_style
                )

    return _to_text(grid, styles)


def canvas_conformance_artifacts(
    canvas: Canvas, buffers: dict[str, CellBuffer], *, display: int = 0
) -> tuple[str, str]:
    """(canvas layout tree, canvas text snapshot) from the TUI's own pipeline.

    The snapshot is taken from the styled workspace render with styling
    stripped, for the same reason the single-screen version is: it proves
    what this renderer actually draws, not that a second code path agrees.
    """
    styled = render_canvas_styled(canvas, buffers, DEFAULT_THEME, display=display, draw_panes=False)
    lines = [line.rstrip() for line in styled.plain.split("\n")]
    trailer = canvas_text_snapshot(canvas, buffers, display=display).rstrip("\n").split("\n")
    # The trailer (unplaced components, other displays) is workspace
    # bookkeeping rather than something drawn on the grid, so it is appended
    # from the shared projection instead of re-derived here.
    body_height = len(lines)
    text = "\n".join([*lines, *trailer[body_height:]]).rstrip("\n") + "\n"
    return canvas_layout_tree(canvas, buffers), text


def conformance_artifacts(buffer: CellBuffer) -> tuple[str, str]:
    """(layout tree, text snapshot) as the conformance suite requires.

    The text snapshot is taken from the *styled* render with styling
    stripped, so this proves the TUI's own pipeline produces the agreed
    output — not that a second code path happens to agree.
    """
    styled = render_styled(buffer, DEFAULT_THEME, draw_panes=False)
    text = "\n".join(line.rstrip() for line in styled.plain.split("\n"))
    return layout_tree(buffer), text.rstrip("\n") + "\n"
