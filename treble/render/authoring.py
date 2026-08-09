"""Layout authoring as commands, not gestures (spec §5.3, §4.2).

`render/layout.py` was built, tested and unreachable, and the reason
recorded against it was that "the desktop shell's drag and resize gestures
are the caller and are not built". That reason assumed a mouse. This
workstation's primary input is a command line — §5.2 makes `<GO>` the
deliberate commit step — and one of its two renderers is a terminal, where
there are no gestures to build and never will be.

So the caller is `CNVS MOVE`, `CNVS SIZE`, `CNVS SAVE`, `CNVS LOAD`. That
choice is not a workaround for the missing gestures. It is the shape I6
requires: a gesture handler would have lived in the desktop shell and the
TUI would have been left without layout authoring at all, which is exactly
the "one screen definition, many renderers" failure the invariant exists to
prevent. Gestures can be added later as a second way to call these same
functions, and they will not be a second implementation.

Everything here is a pure function of (canvas, arguments) returning a new
canvas and a status line. Neither renderer owns the behaviour, so neither
can drift from the other.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from treble.render.canvas import Canvas, CanvasComponent
from treble.render.layout import (
    LayoutError,
    load_layout,
    move_component,
    offscreen_components,
    resize_component,
    save_layout,
)

#: The verbs `CNVS` accepts after its mnemonic. `CNVS` alone still draws the
#: workspace, so an existing muscle-memory command is unchanged.
VERBS = ("MOVE", "SIZE", "SAVE", "LOAD")

#: Layout files live beside the store, one JSON file per named layout.
LAYOUT_SUBDIR = "layouts"


class LayoutOutcome(BaseModel):
    """What a layout command did.

    `canvas` is None when nothing changed — a refusal, or a command that
    only reported. A renderer must not swap in a canvas on a refusal, and
    returning the old one would make "unchanged" and "changed back" the
    same value.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    canvas: Canvas | None
    status: str


def layouts_dir(data_dir: Path) -> Path:
    """Where named layouts are written."""
    return data_dir / LAYOUT_SUBDIR


def _layout_path(data_dir: Path, name: str) -> Path:
    # Rejected rather than sanitised. A name containing a separator is a
    # user error, and quietly rewriting it means SAVE and LOAD disagree
    # about where the file went — the layout is then lost with no error at
    # either end.
    if "/" in name or "\\" in name or name in {"", ".", ".."}:
        raise LayoutError(
            f"{name!r} is not a usable layout name: it must not contain a path separator"
        )
    return layouts_dir(data_dir) / f"{name}.json"


def _components(canvas: Canvas) -> list[CanvasComponent]:
    return [canvas.component(cid) for cid in canvas.component_ids]


def _rebuilt(canvas: Canvas, components: tuple[CanvasComponent, ...]) -> Canvas:
    """A new canvas with the same link state and the new placements."""
    return canvas.with_placements(components)


def _ints(values: tuple[str, ...], names: tuple[str, str]) -> tuple[int, int]:
    try:
        first, second = (int(value) for value in values)
    except ValueError as exc:
        raise LayoutError(
            f"{names[0]} and {names[1]} must be whole numbers of grid cells, not "
            f"{values[0]!r} and {values[1]!r}. Cells rather than pixels, because a "
            "layout saved in pixels only reconstructs on the display it was saved from"
        ) from exc
    return first, second


def apply_layout_command(
    canvas: Canvas | None, arguments: tuple[str, ...], *, data_dir: Path
) -> LayoutOutcome:
    """Run one `CNVS <verb> …` command.

    Returns rather than raises: a refusal is a status line a user reads, not
    a traceback. The distinction that matters is that a refusal leaves
    `canvas` None, so a caller cannot accidentally commit a rejected move.
    """
    verb = arguments[0].upper()
    rest = arguments[1:]
    if canvas is None:
        return LayoutOutcome(
            canvas=None,
            status=f"CNVS {verb}: no canvas configured, so there is no layout to change.",
        )
    try:
        return _dispatch(canvas, verb, rest, data_dir=data_dir)
    except LayoutError as exc:
        # The message is the point. Every refusal in layout.py says what it
        # refused and why, and swallowing that into "invalid" would leave a
        # user guessing at a rule the system already knows.
        return LayoutOutcome(canvas=None, status=f"CNVS {verb}: {exc}")


def _dispatch(canvas: Canvas, verb: str, rest: tuple[str, ...], *, data_dir: Path) -> LayoutOutcome:
    if verb == "MOVE":
        if len(rest) != 3:
            raise LayoutError("usage: CNVS MOVE <component> <x> <y>")
        x, y = _ints(rest[1:], ("x", "y"))
        moved = move_component(_components(canvas), rest[0], x=x, y=y)
        return LayoutOutcome(
            canvas=_rebuilt(canvas, moved), status=f"CNVS MOVE: {rest[0]} to ({x}, {y})."
        )

    if verb == "SIZE":
        if len(rest) != 3:
            raise LayoutError("usage: CNVS SIZE <component> <width> <height>")
        width, height = _ints(rest[1:], ("width", "height"))
        sized = resize_component(_components(canvas), rest[0], width=width, height=height)
        return LayoutOutcome(
            canvas=_rebuilt(canvas, sized),
            status=f"CNVS SIZE: {rest[0]} to {width}x{height}.",
        )

    if verb == "SAVE":
        if len(rest) != 1:
            raise LayoutError("usage: CNVS SAVE <name>")
        components = _components(canvas)
        placed = [c for c in components if c.placement is not None]
        if not placed:
            raise LayoutError(
                "nothing on this canvas has a placement, so there is no arrangement to "
                "save. Saving an empty layout would overwrite a good one with a file "
                "that restores to nothing"
            )
        save_layout(_layout_path(data_dir, rest[0]), rest[0], components)
        return LayoutOutcome(
            canvas=None,
            status=f"CNVS SAVE: {len(placed)} placed components written as {rest[0]!r}.",
        )

    if verb == "LOAD":
        if len(rest) != 1:
            raise LayoutError("usage: CNVS LOAD <name>")
        saved = load_layout(_layout_path(data_dir, rest[0]))
        loaded = Canvas(list(saved.components))
        # Reported, never reflowed. A user who loads a two-monitor layout on
        # a laptop and gets everything squashed onto one screen has lost the
        # arrangement, and reconnecting the monitor does not bring it back.
        displays = max(1, len(loaded.displays()))
        stranded = offscreen_components(saved.components, displays=displays)
        note = (
            ""
            if not stranded
            else (
                f" {len(stranded)} on a display this machine does not have, left where "
                "they were rather than reflowed."
            )
        )
        return LayoutOutcome(
            canvas=loaded,
            status=f"CNVS LOAD: {rest[0]!r}, {len(saved.components)} components.{note}",
        )

    raise LayoutError(f"unknown verb {verb!r}; expected one of {', '.join(VERBS)}")


__all__ = ["VERBS", "LayoutOutcome", "apply_layout_command", "layouts_dir"]
