"""The Textual workstation (spec §4 TUI surface, §3.3 panel model).

Phase 1 is a single panel with a command line, which is the "single-panel /
laptop mode" §3.3 describes. The four-panel classic layout and Canvas
(§5.3) are Phase 2; the screen contract already supports them, so adding
panels is layout work rather than a rewrite.

Keyboard-first (§design principle 2): the command line has focus, `<GO>` is
Enter, and nothing is required from the mouse.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, Static

from treble.cmd.grammar import CommandKind, parse_command
from treble.render.contract.buffer import CellBuffer
from treble.render.contract.registry import get_screen, has_screen
from treble.render.contract.resolver import ScreenContext, TapiView, resolve
from treble.render.tui.renderer import render_styled
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

    def __init__(self, tapi: TapiView, *, theme: Theme = DEFAULT_THEME) -> None:
        super().__init__()
        self._tapi = tapi
        self._theme = theme
        #: The buffer most recently rendered, and the last status text.
        #: Exposed so the app's behaviour can be asserted directly rather
        #: than by scraping widget internals, which change between
        #: Textual versions.
        self.last_buffer: CellBuffer | None = None
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
