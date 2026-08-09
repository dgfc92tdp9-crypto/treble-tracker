"""The Textual workstation (spec §4 TUI surface, §3.3 panel model).

Phase 1 is a single panel with a command line, which is the "single-panel /
laptop mode" §3.3 describes. `CNVS` now draws a whole workspace into that
panel (§5.3): a terminal has one grid and no windows, so the components are
composited into it rather than floating, and one display is shown at a time
because overlaying two displays' placements would collide arbitrary
components. The four-panel classic layout remains Phase 2.

Keyboard-first (§design principle 2): the command line has focus, `<GO>` is
Enter, and nothing is required from the mouse.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, Static

from treble.cmd.grammar import CommandKind, parse_command
from treble.cmd.paths import DEFAULT_DATA_DIR
from treble.render.authoring import apply_layout_command
from treble.render.canvas import Canvas, resolve_canvas
from treble.render.contract.buffer import CellBuffer
from treble.render.contract.registry import get_screen, has_screen
from treble.render.contract.resolver import ScreenContext, TapiView, resolve
from treble.render.tui.renderer import render_canvas_styled, render_styled
from treble.render.tui.theme import DEFAULT_THEME, Theme

WELCOME = """\
TREBLE TRACKER

Type a command and press Enter. <GO> is Enter.

  IBM US Equity DES        description of IBM common stock
  AAPL US Equity DES       any populated filer
  FLDS                     field finder

Ctrl+Q quits.\
"""


class Workstation(App[None]):
    """One panel: a screen area above, a command line below."""

    CSS = """
    Screen { background: #000000; }
    #screen-area { height: 1fr; padding: 0 1; }
    #command-line { dock: bottom; border: none; background: #101010; }
    #status { dock: bottom; height: 1; color: #767676; padding: 0 1; }
    """

    # ClassVar: Textual reads BINDINGS off the class, and RUF012 is right
    # that a bare mutable class attribute is a hazard.
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("ctrl+q", "quit", "Quit", priority=True)
    ]

    def __init__(
        self,
        tapi: TapiView,
        *,
        theme: Theme = DEFAULT_THEME,
        canvas: Canvas | None = None,
        data_dir: Path = DEFAULT_DATA_DIR,
    ) -> None:
        super().__init__()
        self._tapi = tapi
        self._theme = theme
        #: No default workspace, matching the server: an empty canvas and an
        #: unconfigured one look identical on screen, so `CNVS` says which.
        self._canvas = canvas
        #: Where named layouts are written and read.
        self._data_dir = data_dir
        #: The buffer most recently rendered, and the last status text.
        #: Exposed so the app's behaviour can be asserted directly rather
        #: than by scraping widget internals, which change between
        #: Textual versions.
        self.last_buffer: CellBuffer | None = None
        #: The workspace most recently drawn, for the same reason: widget
        #: internals change between Textual versions, so behaviour is
        #: asserted through the app rather than by scraping the widget.
        self.last_canvas: Text | None = None
        self.last_status: str = ""

    def compose(self) -> ComposeResult:
        yield Vertical(Static(WELCOME, id="screen-area"))
        yield Static("", id="status")
        yield Input(placeholder="command…", id="command-line")

    def on_mount(self) -> None:
        self.query_one("#command-line", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter is `<GO>` — the deliberate commit step (§5.2)."""
        self._execute(event.value)
        event.input.value = ""

    def _say(self, widget: Static, message: str) -> None:
        self.last_status = message
        widget.update(message)

    def _author_layout(self, arguments: tuple[str, ...], area: Static, status: Static) -> None:
        """`CNVS MOVE|SIZE|SAVE|LOAD` — layout authoring from the command line.

        The terminal has no drag gestures and never will, so a workstation
        whose layout could only be authored by mouse would have one renderer
        that cannot arrange its own workspace. The behaviour lives in
        `render.authoring`, shared with the HTTP surface, so the two cannot
        answer differently.
        """
        outcome = apply_layout_command(self._canvas, arguments, data_dir=self._data_dir)
        if outcome.canvas is not None:
            self._canvas = outcome.canvas
            self._draw_canvas(area, status)
        # Said after the redraw: _draw_canvas writes its own status, and
        # setting this first would have it overwritten by "N components"
        # while the user is looking for whether their move was accepted.
        self._say(status, outcome.status)

    def _draw_canvas(self, area: Static, status: Static) -> None:
        """`CNVS` — the whole workspace in the panel (§5.3).

        The three states that would each render as an empty panel are
        distinguished, exactly as the HTTP surface distinguishes them: no
        canvas configured, a configured canvas with no components, and a
        component that failed to resolve. An unexplained blank panel and a
        lost layout are indistinguishable otherwise.
        """
        if self._canvas is None:
            self._say(
                status,
                "CNVS: no canvas configured. An empty workspace and an unconfigured one "
                "look the same on screen, so this says which.",
            )
            return
        if not self._canvas.component_ids:
            self._say(status, "CNVS: the canvas has no components yet.")
            return
        try:
            buffers = resolve_canvas(self._canvas, tapi=self._tapi, as_of=datetime.now(UTC))
            rendered = render_canvas_styled(self._canvas, buffers, self._theme)
        except Exception as exc:
            self._say(status, f"CNVS: {type(exc).__name__}: {exc}")
            return

        # `last_buffer` stays as it was: a canvas is not one buffer, and
        # leaving a stale single screen there would let a caller read the
        # wrong thing back.
        self.last_canvas = rendered
        area.update(rendered)
        linked = sum(1 for cid in self._canvas.component_ids if self._canvas.component(cid).channel)
        self._say(
            status,
            f"CNVS  ·  {len(self._canvas.component_ids)} components, {linked} linked",
        )

    def _execute(self, line: str) -> None:
        area = self.query_one("#screen-area", Static)
        status = self.query_one("#status", Static)
        parsed = parse_command(line)

        if parsed.kind is CommandKind.EMPTY:
            return
        if parsed.kind is CommandKind.ASK:
            # §5.2/§20.2: never a dead end. ASK itself is Phase 5, so say
            # so plainly rather than pretending to answer.
            self._say(
                status, f"ASK: {parsed.ask_reason} — the natural-language interface is Phase 5."
            )
            return
        if parsed.kind is CommandKind.SECURITY_MENU:
            self._say(
                status,
                f"{parsed.security.display() if parsed.security else ''}: "
                "menu navigation is not built yet; type a function, e.g. DES.",
            )
            return
        if parsed.function == "CNVS":
            if parsed.arguments:
                self._author_layout(parsed.arguments, area, status)
            else:
                self._draw_canvas(area, status)
            return

        if parsed.function is None or not has_screen(parsed.function):
            self._say(
                status,
                f"{parsed.function or line!r}: no screen definition yet (Phase 1 ships DES first).",
            )
            return

        try:
            buffer = resolve(
                get_screen(parsed.function),
                ScreenContext(security=parsed.security),
                as_of=datetime.now(UTC),
                tapi=self._tapi,
            )
        except Exception as exc:
            # A screen error must not kill the session; the user needs
            # the reason on the status line, not a traceback.
            self._say(status, f"{type(exc).__name__}: {exc}")
            return

        self.last_buffer = buffer
        area.update(render_styled(buffer, self._theme))
        stale_note = "  ·  contains stale values" if buffer.stale else ""
        self._say(
            status,
            f"{buffer.mnemonic}  ·  "
            f"{parsed.security.display() if parsed.security else 'global'}{stale_note}",
        )


def run(tapi: TapiView, *, theme: Theme = DEFAULT_THEME) -> None:  # pragma: no cover
    """Launch the workstation. Covered by the CLI smoke path, not unit tests:
    driving a real terminal app in-process is what the conformance suite
    already proves about rendering."""
    Workstation(tapi, theme=theme).run()
