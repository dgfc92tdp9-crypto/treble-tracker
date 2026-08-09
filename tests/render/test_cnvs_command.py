"""`CNVS <GO>` — serving a whole workspace (spec §5.3).

A canvas is many screens at once, so it cannot travel in the single-screen
`buffer` field. These tests are mostly about the states that would otherwise
render as an empty rectangle and let a user conclude their layout was lost.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from treble.render.canvas import Canvas, CanvasComponent, Channel, Placement
from treble.render.server import create_app
from treble.store.duck import DuckStore
from treble.tapi.local import LocalTapi


def _client(canvas: Canvas | None, tmp_path: Path) -> TestClient:
    return TestClient(create_app(LocalTapi(DuckStore(tmp_path / "t.db")), canvas=canvas))


def _workspace() -> Canvas:
    return Canvas(
        [
            CanvasComponent(
                id="left",
                screen="ICVS",
                channel=Channel.RED,
                placement=Placement(x=0, y=0, width=40, height=22),
            ),
            CanvasComponent(
                id="right",
                screen="MDL",
                placement=Placement(x=40, y=0, width=40, height=22),
            ),
        ]
    )


class TestServingACanvas:
    def test_every_component_comes_back_with_its_buffer(self, tmp_path: Path) -> None:
        body = _client(_workspace(), tmp_path).post("/command", json={"line": "CNVS"}).json()
        assert body["canvas"] is not None
        assert [c["id"] for c in body["canvas"]] == ["left", "right"]
        assert all(c["tree"] for c in body["canvas"])

    def test_placement_and_link_travel_with_each_component(self, tmp_path: Path) -> None:
        """A client that received buffers without placements would have to
        invent a layout, and without channels would render a workspace whose
        links are invisible."""
        body = _client(_workspace(), tmp_path).post("/command", json={"line": "CNVS"}).json()
        left = next(c for c in body["canvas"] if c["id"] == "left")
        assert left["placement"] == {"x": 0, "y": 0, "width": 40, "height": 22, "display": 0}
        assert left["channel"] == "red"
        assert next(c for c in body["canvas"] if c["id"] == "right")["channel"] is None

    def test_the_status_line_says_how_many_are_linked(self, tmp_path: Path) -> None:
        body = _client(_workspace(), tmp_path).post("/command", json={"line": "CNVS"}).json()
        assert "2 components, 1 linked" in body["status"]

    def test_the_single_screen_buffer_field_stays_empty(self, tmp_path: Path) -> None:
        """A client falling back to `buffer` would show one screen where a
        workspace was asked for."""
        body = _client(_workspace(), tmp_path).post("/command", json={"line": "CNVS"}).json()
        assert body["buffer"] is None


class TestTheStatesThatWouldLookLikeAnEmptyWorkspace:
    def test_no_canvas_configured_says_so(self, tmp_path: Path) -> None:
        """An unconfigured server and an empty workspace look identical on
        screen, so the two must not return the same thing."""
        body = _client(None, tmp_path).post("/command", json={"line": "CNVS"}).json()
        assert body["canvas"] is None
        assert "no canvas configured" in body["status"]

    def test_a_configured_but_empty_canvas_says_something_different(self, tmp_path: Path) -> None:
        body = _client(Canvas([]), tmp_path).post("/command", json={"line": "CNVS"}).json()
        assert body["canvas"] is None
        assert "no components yet" in body["status"]

    def test_a_broken_component_reports_the_reason(self, tmp_path: Path) -> None:
        """One unrenderable component must not blank the whole workspace
        silently — the status carries why."""
        broken = Canvas([CanvasComponent(id="x", screen="NOSUCHSCREEN")])
        body = _client(broken, tmp_path).post("/command", json={"line": "CNVS"}).json()
        assert body["canvas"] is None
        assert "CNVS:" in body["status"] and "NOSUCHSCREEN" in body["status"]


class TestTheSingleScreenPathIsUnchanged:
    def test_an_ordinary_function_still_returns_one_buffer(self, tmp_path: Path) -> None:
        body = _client(_workspace(), tmp_path).post("/command", json={"line": "ICVS"}).json()
        assert body["buffer"] is not None
        assert body["canvas"] is None

    def test_an_unknown_function_is_still_explained(self, tmp_path: Path) -> None:
        """Adding the CNVS branch must not swallow anything ahead of it.

        `ZZZZ` is routed to ASK by the grammar rather than to the screen
        lookup — it is not a known function and carries no asset-class key,
        so it cannot be resolved as a security either. Asserted as "explains
        itself and returns no canvas" rather than by matching that wording,
        because the first version of this test asserted a message I had
        assumed rather than read."""
        body = _client(_workspace(), tmp_path).post("/command", json={"line": "ZZZZ"}).json()
        assert body["status"], "an unrecognised line must never come back silent"
        assert body["canvas"] is None
        assert body["buffer"] is None


class TestLayoutAuthoringReachesBothRenderers:
    """I6, applied to a behaviour rather than to a screen.

    Layout authoring was recorded as blocked on the desktop shell's drag
    gestures. Had it been built that way, the terminal — which has no
    gestures and never will — would have been the one renderer unable to
    arrange its own workspace. Both surfaces now call the same function,
    and the test that keeps them honest is that they say the same thing.
    """

    @staticmethod
    def _canvas() -> Canvas:
        return Canvas(
            [
                CanvasComponent(
                    id="DES1",
                    screen="DES",
                    channel=Channel.BLUE,
                    placement=Placement(x=0, y=0, width=40, height=10),
                ),
                CanvasComponent(
                    id="YAS1",
                    screen="YAS",
                    channel=Channel.BLUE,
                    placement=Placement(x=40, y=0, width=40, height=10),
                ),
            ]
        )

    def test_the_http_surface_moves_a_component(self, tmp_path: Path) -> None:
        client = TestClient(
            create_app(
                LocalTapi(DuckStore(tmp_path / "t.db")),
                canvas=self._canvas(),
                data_dir=tmp_path,
            )
        )
        reply = client.post("/command", json={"line": "CNVS MOVE DES1 0 20"})
        assert reply.status_code == 200
        assert "(0, 20)" in reply.json()["status"]

    def test_the_move_is_kept_for_the_next_command(self, tmp_path: Path) -> None:
        """A move the server forgets is a move that never happened. The
        canvas is a closure variable, so this is the assertion that the
        rebound one is what the next request sees."""
        client = TestClient(
            create_app(
                LocalTapi(DuckStore(tmp_path / "t.db")),
                canvas=self._canvas(),
                data_dir=tmp_path,
            )
        )
        client.post("/command", json={"line": "CNVS MOVE DES1 0 20"})
        client.post("/command", json={"line": "CNVS SAVE desk"})
        assert (tmp_path / "layouts" / "desk.json").exists()
        # 0,20 rather than 0,0: the saved file must hold the moved position.
        assert '"y": 20' in (tmp_path / "layouts" / "desk.json").read_text()

    def test_a_refused_move_leaves_the_server_canvas_alone(self, tmp_path: Path) -> None:
        client = TestClient(
            create_app(
                LocalTapi(DuckStore(tmp_path / "t.db")),
                canvas=self._canvas(),
                data_dir=tmp_path,
            )
        )
        reply = client.post("/command", json={"line": "CNVS MOVE DES1 40 0"})
        assert "overlap" in reply.json()["status"].lower()
        client.post("/command", json={"line": "CNVS SAVE desk"})
        assert '"x": 0' in (tmp_path / "layouts" / "desk.json").read_text()

    def test_bare_cnvs_still_draws_the_workspace(self, tmp_path: Path) -> None:
        """The verb is optional. An existing command must not have changed
        meaning because authoring was added beside it."""
        client = TestClient(
            create_app(
                LocalTapi(DuckStore(tmp_path / "t.db")),
                canvas=self._canvas(),
                data_dir=tmp_path,
            )
        )
        reply = client.post("/command", json={"line": "CNVS"})
        assert "MOVE" not in reply.json()["status"]

    def test_both_renderers_give_the_same_answer(self, tmp_path: Path) -> None:
        """The one that would catch a second implementation. A gesture
        handler in the shell and a command handler in the terminal would
        both work, and would drift apart on the first refusal."""
        import asyncio

        from treble.render.tui.app import Workstation

        line = "CNVS MOVE DES1 40 0"
        client = TestClient(
            create_app(
                LocalTapi(DuckStore(tmp_path / "h.db")),
                canvas=self._canvas(),
                data_dir=tmp_path,
            )
        )
        http_status = client.post("/command", json={"line": line}).json()["status"]

        async def drive() -> str:
            app = Workstation(
                LocalTapi(DuckStore(tmp_path / "t.db")),
                canvas=self._canvas(),
                data_dir=tmp_path,
            )
            async with app.run_test(size=(90, 30)) as pilot:
                await pilot.press(*line)
                await pilot.press("enter")
                await pilot.pause()
            return app.last_status

        tui_status = asyncio.run(drive())
        assert tui_status == http_status
        assert "overlap" in tui_status.lower()
