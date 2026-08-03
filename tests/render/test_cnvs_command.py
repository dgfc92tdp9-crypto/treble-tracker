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
        assert all(c["buffer"] for c in body["canvas"])

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
