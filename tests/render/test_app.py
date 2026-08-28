"""The workstation app, driven headlessly (spec §4, §5.2).

These exercise the real Textual app: typing into the command line, pressing
Enter as `<GO>`, and asserting what the user would see. The screen contents
come from the app's own `last_buffer`, so the assertions are about
behaviour rather than widget internals.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from treble.ingest.base import RawPayload
from treble.ingest.edgar import EdgarCompanyFactsAdapter
from treble.render.canvas import (
    Canvas,
    CanvasComponent,
    Channel,
    Fdc3Instrument,
    Placement,
)
from treble.render.tui.app import Workstation
from treble.store.duck import DuckStore
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash
from treble.tapi.local import LocalTapi, TickerIndex

FIXTURES = Path(__file__).parent.parent / "fixtures"
COMPANYFACTS = FIXTURES / "edgar" / "companyfacts_CIK0000051143.json"
FETCHED = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


def an_unbuilt_mnemonic() -> str:
    """A function the grammar knows but no screen implements yet.

    Derived rather than hard-coded: this test named YAS, and silently
    started asserting the wrong thing the moment YAS was built. Anything
    still outstanding works, and the test retires itself when the last
    screen lands.
    """
    from treble.cmd.grammar import KNOWN_MNEMONICS
    from treble.render.contract.registry import available

    outstanding = sorted(set(KNOWN_MNEMONICS) - set(available()))
    if not outstanding:
        pytest.skip("every known mnemonic now has a screen definition")
    return outstanding[0]


pytestmark = pytest.mark.asyncio


@pytest.fixture
def tapi(tmp_path: Path) -> LocalTapi:
    store = DuckStore(tmp_path / "t.db")
    adapter = EdgarCompanyFactsAdapter(
        PayloadStore(tmp_path / "p"),
        IngestLog(tmp_path / "l.db"),
        ciks=(51143,),
        contact_email="test@example.com",
    )
    raw = RawPayload(data=COMPANYFACTS.read_bytes(), source_uri="fixture://cf", fetched_at=FETCHED)
    batch = adapter.parse(raw, payload_hash(raw.data))
    store.write_provenance(list(batch.provenance))
    store.write_facts(list(batch.facts))
    return LocalTapi(store, tickers=TickerIndex({"IBM": 51143}))


async def submit(app: Workstation, pilot, line: str) -> None:  # type: ignore[no-untyped-def]
    app.query_one("#command-line").value = line
    await pilot.press("enter")
    await pilot.pause()


class TestCommandExecution:
    async def test_des_renders_real_figures(self, tapi: LocalTapi) -> None:
        app = Workstation(tapi)
        async with app.run_test(size=(90, 30)) as pilot:
            await submit(app, pilot, "IBM US Equity DES")

        assert app.last_buffer is not None
        assert app.last_buffer.mnemonic == "DES"
        # The figure on screen must be IBM's actually-reported number.
        doc = json.loads(COMPANYFACTS.read_bytes())
        rows = doc["facts"]["us-gaap"]["Assets"]["units"]["USD"]
        expected = max(rows, key=lambda r: r["end"])["val"]
        texts = {c.text.strip() for c in app.last_buffer.cells}
        assert f"{float(expected):,.0f}" in texts

    async def test_enter_is_the_go_token(self, tapi: LocalTapi) -> None:
        """Typing without pressing Enter must render nothing (§5.2: the
        command line is editable state until submitted)."""
        app = Workstation(tapi)
        async with app.run_test(size=(90, 30)) as pilot:
            app.query_one("#command-line").value = "IBM US Equity DES"
            await pilot.pause()
            assert app.last_buffer is None
            await pilot.press("enter")
            await pilot.pause()
            assert app.last_buffer is not None

    async def test_command_line_clears_after_submission(self, tapi: LocalTapi) -> None:
        app = Workstation(tapi)
        async with app.run_test(size=(90, 30)) as pilot:
            await submit(app, pilot, "IBM US Equity DES")
            assert app.query_one("#command-line").value == ""

    async def test_unresolvable_input_reports_ask_not_an_error(self, tapi: LocalTapi) -> None:
        # §5.2/§20.2: a user never hits a dead end.
        app = Workstation(tapi)
        async with app.run_test(size=(90, 30)) as pilot:
            await submit(app, pilot, "what is IBM worth")
        assert app.last_status.startswith("ASK:")
        assert app.last_buffer is None

    async def test_unknown_ticker_reports_the_reason_and_survives(self, tapi: LocalTapi) -> None:
        # A screen error must not kill the session.
        app = Workstation(tapi)
        async with app.run_test(size=(90, 30)) as pilot:
            await submit(app, pilot, "ZZZZ US Equity DES")
            assert "company index" in app.last_status
            # …and the app still works afterwards.
            await submit(app, pilot, "IBM US Equity DES")
            assert app.last_buffer is not None

    async def test_function_without_a_screen_says_so(self, tapi: LocalTapi) -> None:
        app = Workstation(tapi)
        async with app.run_test(size=(90, 30)) as pilot:
            await submit(app, pilot, f"IBM US Equity {an_unbuilt_mnemonic()}")
        assert "no screen definition yet" in app.last_status

    async def test_security_alone_explains_the_menu_is_pending(self, tapi: LocalTapi) -> None:
        app = Workstation(tapi)
        async with app.run_test(size=(90, 30)) as pilot:
            await submit(app, pilot, "IBM US Equity")
        assert "menu navigation" in app.last_status

    async def test_empty_submission_does_nothing(self, tapi: LocalTapi) -> None:
        app = Workstation(tapi)
        async with app.run_test(size=(90, 30)) as pilot:
            await submit(app, pilot, "")
        assert app.last_buffer is None
        assert app.last_status == ""

    async def test_status_names_the_screen_and_security(self, tapi: LocalTapi) -> None:
        app = Workstation(tapi)
        async with app.run_test(size=(90, 30)) as pilot:
            await submit(app, pilot, "IBM US Equity DES")
        assert "DES" in app.last_status
        assert "IBM US Equity" in app.last_status


class TestCanvasInTheTui:
    """`CNVS` in the terminal (§5.3).

    The TUI participates in colour groups like any other surface — that was
    settled when the Canvas was designed — but until now nothing here drew
    one. A workspace the renderer can composite and the client never calls
    is a feature that exists only in its tests.
    """

    async def test_no_canvas_configured_says_so(self, tapi: LocalTapi) -> None:
        """Not a blank panel. An unconfigured workspace and a lost layout
        are indistinguishable otherwise — the same distinction the HTTP
        surface makes."""
        app = Workstation(tapi)
        async with app.run_test(size=(90, 30)) as pilot:
            await submit(app, pilot, "CNVS")
        assert "no canvas configured" in app.last_status

    async def test_a_configured_but_empty_canvas_is_a_different_message(
        self, tapi: LocalTapi
    ) -> None:
        app = Workstation(tapi, canvas=Canvas())
        async with app.run_test(size=(90, 30)) as pilot:
            await submit(app, pilot, "CNVS")
        assert "no components yet" in app.last_status
        assert "no canvas configured" not in app.last_status

    async def test_a_workspace_draws_every_component_framed(self, tapi: LocalTapi) -> None:
        canvas = Canvas(
            [
                CanvasComponent(
                    id="left",
                    screen="DES",
                    channel=Channel.RED,
                    placement=Placement(x=0, y=0, width=44, height=14),
                ),
                CanvasComponent(
                    id="right",
                    screen="FLDS",
                    channel=Channel.BLUE,
                    placement=Placement(x=44, y=0, width=44, height=14),
                ),
            ]
        )
        canvas.broadcast("left", Fdc3Instrument(ticker="IBM"))
        app = Workstation(tapi, canvas=canvas)
        async with app.run_test(size=(90, 30)) as pilot:
            await submit(app, pilot, "CNVS")

        assert app.last_status == "CNVS  ·  2 components, 2 linked"
        assert app.last_canvas is not None
        text = app.last_canvas.plain
        # Both frames, each naming its screen and its colour group: a link
        # the user cannot see is a link they cannot verify.
        assert "+ DES red" in text
        assert "+ FLDS blue" in text
        # And the red broadcast reached the DES component: the figure drawn
        # is IBM's actually-reported one, derived from the fixture rather
        # than pinned, so this cannot pass against a different company or a
        # component that resolved with no selection at all.
        doc = json.loads(COMPANYFACTS.read_bytes())
        rows = doc["facts"]["us-gaap"]["Assets"]["units"]["USD"]
        expected = max(rows, key=lambda r: r["end"])["val"]
        assert f"{float(expected):,.0f}" in text

    async def test_a_component_too_small_to_draw_reports_rather_than_blanks(
        self, tapi: LocalTapi
    ) -> None:
        """A 2x2 frame has no interior. Drawing it would put an empty box on
        the workspace, which reads as a component whose data is missing."""
        canvas = Canvas(
            [
                CanvasComponent(
                    id="tiny",
                    screen="FLDS",
                    placement=Placement(x=0, y=0, width=2, height=2),
                )
            ]
        )
        app = Workstation(tapi, canvas=canvas)
        async with app.run_test(size=(90, 30)) as pilot:
            await submit(app, pilot, "CNVS")
        assert "too small to draw anything" in app.last_status
        assert app.last_status.startswith("CNVS: ValueError")

    async def test_a_canvas_does_not_overwrite_the_last_single_screen(
        self, tapi: LocalTapi
    ) -> None:
        """`last_buffer` is one screen. Leaving a canvas's components in it
        would let a caller read back a buffer that was never the panel."""
        canvas = Canvas(
            [
                CanvasComponent(
                    id="only",
                    screen="FLDS",
                    placement=Placement(x=0, y=0, width=40, height=12),
                )
            ]
        )
        app = Workstation(tapi, canvas=canvas)
        async with app.run_test(size=(90, 30)) as pilot:
            await submit(app, pilot, "IBM US Equity DES")
            assert app.last_buffer is not None
            assert app.last_buffer.mnemonic == "DES"
            await submit(app, pilot, "CNVS")
            assert app.last_buffer.mnemonic == "DES"
