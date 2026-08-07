"""Authoring a canvas layout: move, resize, save, restore (spec §5.3, §4.2).

`canvas.py` holds a layout and propagates context across it. This is the
part that lets a user *build* one — the operations behind a drag and a
resize, and the persistence that makes a workspace survive a restart.

The mouse handling belongs to each renderer; the rules do not. Putting them
here means the TUI, the web renderer and the desktop shell cannot disagree
about whether a drop was legal, which is the same reason screen definitions
are shared rather than reimplemented (I6).

**A move that would overlap is refused, not adjusted.** A layout manager
that nudges a window aside has decided something the user did not ask for,
and the next drag starts from a position they did not choose. Refusing
returns the caller a reason it can show; adjusting silently makes the
canvas feel possessed.

**Nothing is mutated in place.** :class:`Canvas` holds frozen components,
and every operation here returns a new tuple of them. A failed drag
therefore cannot leave a layout half-modified, which is the state that
would otherwise persist to disk and be restored as the user's workspace.

**Restoring onto fewer displays keeps the display index.** A component
saved on display 2 and restored on a one-screen machine stays on display 2
and reports as off-screen rather than being reflowed onto display 0. Silent
reflow loses the arrangement permanently: the user reconnects their monitors
and the layout does not come back, because it was rewritten the moment they
were absent. :func:`offscreen_components` is how a renderer offers to bring
them over — the user's choice, made once, rather than the file's.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from treble.render.canvas import CanvasComponent, Placement

#: Smallest a component may be dragged down to. Below this a screen shows
#: its frame and nothing legible, which looks like a rendering fault rather
#: than a small window.
MIN_WIDTH = 20
MIN_HEIGHT = 5

#: Format version written into every saved layout. Read on load and refused
#: if unknown: a layout from a future version restored by an older build
#: would place components by rules that no longer mean the same thing.
LAYOUT_VERSION = 1


class LayoutError(ValueError):
    """An operation that would produce an illegal layout, with the reason."""


class SavedLayout(BaseModel):
    """A layout as written to disk."""

    model_config = ConfigDict(frozen=True)

    version: int = Field(default=LAYOUT_VERSION)
    name: str = Field(min_length=1)
    components: tuple[CanvasComponent, ...]


def _replace(
    components: Sequence[CanvasComponent], component_id: str, placement: Placement
) -> tuple[CanvasComponent, ...]:
    found = False
    out = []
    for component in components:
        if component.id == component_id:
            found = True
            out.append(component.model_copy(update={"placement": placement}))
        else:
            out.append(component)
    if not found:
        raise LayoutError(f"no component {component_id!r} on this canvas to move or resize")
    return tuple(out)


def _check_free(
    components: Sequence[CanvasComponent], component_id: str, placement: Placement
) -> None:
    for other in components:
        if other.id == component_id or other.placement is None:
            continue
        if placement.overlaps(other.placement):
            raise LayoutError(
                f"{component_id!r} would overlap {other.id!r} ({other.screen}). Refused "
                "rather than nudged: a layout manager that moves a window the user did "
                "not drag has decided something for them, and the next drag starts from "
                "a position they did not choose"
            )


def move_component(
    components: Sequence[CanvasComponent], component_id: str, *, x: int, y: int
) -> tuple[CanvasComponent, ...]:
    """Move a component, keeping its size. Refuses an overlap."""
    current = _placement_of(components, component_id)
    moved = current.model_copy(update={"x": x, "y": y})
    _check_free(components, component_id, moved)
    return _replace(components, component_id, moved)


def resize_component(
    components: Sequence[CanvasComponent], component_id: str, *, width: int, height: int
) -> tuple[CanvasComponent, ...]:
    """Resize a component, keeping its origin. Refuses an overlap."""
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        raise LayoutError(
            f"{width}x{height} is below the {MIN_WIDTH}x{MIN_HEIGHT} minimum; a component "
            "that small shows its frame and nothing legible, which reads as a rendering "
            "fault rather than as a small window"
        )
    current = _placement_of(components, component_id)
    resized = current.model_copy(update={"width": width, "height": height})
    _check_free(components, component_id, resized)
    return _replace(components, component_id, resized)


def _placement_of(components: Sequence[CanvasComponent], component_id: str) -> Placement:
    for component in components:
        if component.id == component_id:
            if component.placement is None:
                raise LayoutError(
                    f"{component_id!r} has no placement, so there is nothing to move. A "
                    "component without one participates in link groups but is not on a "
                    "canvas — the TUI's components are like this"
                )
            return component.placement
    raise LayoutError(f"no component {component_id!r} on this canvas to move or resize")


def save_layout(path: Path, name: str, components: Sequence[CanvasComponent]) -> None:
    """Write a layout, atomically.

    Written to a temporary file and renamed, because a workspace half-
    written by an interrupted save is worse than one not saved at all: the
    user loses the arrangement *and* gets no error, since the file exists.
    """
    saved = SavedLayout(name=name, components=tuple(components))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(saved.model_dump_json(indent=2))
    temporary.replace(path)


def load_layout(path: Path) -> SavedLayout:
    """Read a layout, refusing a version this build does not understand."""
    if not path.exists():
        raise LayoutError(f"no layout at {path}")
    saved = SavedLayout.model_validate_json(path.read_text())
    if saved.version != LAYOUT_VERSION:
        raise LayoutError(
            f"layout {path.name} is version {saved.version} and this build writes "
            f"version {LAYOUT_VERSION}. Refused rather than opened: placement rules that "
            "changed between versions would put components somewhere the user never left "
            "them"
        )
    return saved


def offscreen_components(
    components: Sequence[CanvasComponent], *, displays: int
) -> tuple[CanvasComponent, ...]:
    """Components saved on a display this machine does not have.

    Reported rather than reflowed. Silently moving them to display 0 loses
    the arrangement permanently — the user reconnects the monitor and the
    layout does not come back, because it was rewritten while it was gone.
    """
    if displays < 1:
        raise LayoutError("a machine with no displays cannot show a canvas")
    return tuple(
        component
        for component in components
        if component.placement is not None and component.placement.display >= displays
    )


__all__ = [
    "LAYOUT_VERSION",
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "LayoutError",
    "SavedLayout",
    "load_layout",
    "move_component",
    "offscreen_components",
    "resize_component",
    "save_layout",
]
