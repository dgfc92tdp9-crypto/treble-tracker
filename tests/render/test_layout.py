"""Canvas layout authoring (spec §5.3, §4.2).

Four behaviours that would each be wrong in a way the user only notices
later: an overlap silently nudged, a failed drag leaving the layout
half-modified, a save interrupted into an unreadable file, and a layout
restored onto fewer displays being reflowed so the arrangement never comes
back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from treble.render.canvas import CanvasComponent, Placement
from treble.render.layout import (
    LAYOUT_VERSION,
    MIN_HEIGHT,
    MIN_WIDTH,
    LayoutError,
    SavedLayout,
    load_layout,
    move_component,
    offscreen_components,
    resize_component,
    save_layout,
)


def _component(cid: str, x: int, y: int, *, display: int = 0) -> CanvasComponent:
    return CanvasComponent(
        id=cid,
        screen="DES",
        placement=Placement(x=x, y=y, width=40, height=12, display=display),
    )


LAYOUT = (_component("a", 0, 0), _component("b", 50, 0))


class TestMovingAndResizing:
    def test_a_legal_move_lands_where_asked(self) -> None:
        moved = move_component(LAYOUT, "a", x=0, y=20)
        placement = next(c.placement for c in moved if c.id == "a")
        assert placement is not None
        assert (placement.x, placement.y) == (0, 20)
        assert (placement.width, placement.height) == (40, 12)

    def test_an_overlap_is_refused_not_nudged(self) -> None:
        """A layout manager that moves a window the user did not drag has
        decided something for them, and the next drag starts from a
        position they did not choose."""
        with pytest.raises(LayoutError, match="would overlap"):
            move_component(LAYOUT, "a", x=45, y=0)

    def test_a_refused_move_leaves_the_layout_untouched(self) -> None:
        """Nothing is mutated in place. A half-modified layout is the state
        that would persist to disk and be restored as the workspace."""
        before = [c.placement for c in LAYOUT]
        with pytest.raises(LayoutError):
            move_component(LAYOUT, "a", x=45, y=0)
        assert [c.placement for c in LAYOUT] == before

    def test_resizing_into_a_neighbour_is_refused(self) -> None:
        with pytest.raises(LayoutError, match="would overlap"):
            resize_component(LAYOUT, "a", width=60, height=12)

    def test_a_component_cannot_be_shrunk_below_legibility(self) -> None:
        """Below the minimum a screen shows its frame and nothing legible,
        which reads as a rendering fault rather than a small window."""
        with pytest.raises(LayoutError, match="minimum"):
            resize_component(LAYOUT, "a", width=MIN_WIDTH - 1, height=MIN_HEIGHT)

    def test_moving_onto_another_display_does_not_collide(self) -> None:
        """Two components at the same coordinates on different screens are
        not overlapping, and treating them as such would make a second
        monitor useless."""
        pair = (_component("a", 0, 0), _component("b", 0, 0, display=1))
        assert move_component(pair, "a", x=0, y=0)

    def test_an_unknown_component_is_an_error(self) -> None:
        with pytest.raises(LayoutError, match="no component"):
            move_component(LAYOUT, "ghost", x=0, y=0)

    def test_a_component_without_a_placement_cannot_be_moved(self) -> None:
        """It participates in link groups but is not on a canvas -- the
        TUI's components are like this."""
        unplaced = (CanvasComponent(id="t", screen="DES"),)
        with pytest.raises(LayoutError, match="no placement"):
            move_component(unplaced, "t", x=1, y=1)


class TestPersistence:
    def test_a_saved_layout_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "work.json"
        save_layout(path, "trading", LAYOUT)
        restored = load_layout(path)
        assert restored.name == "trading"
        assert restored.components == LAYOUT

    def test_the_save_leaves_no_temporary_behind(self, tmp_path: Path) -> None:
        path = tmp_path / "work.json"
        save_layout(path, "trading", LAYOUT)
        assert not list(tmp_path.glob("*.partial"))

    def test_an_interrupted_save_leaves_the_previous_layout_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The property atomicity actually buys, tested by interrupting one.

        The first version of this test only asserted that no `.partial`
        file was left behind -- which is equally true of a plain
        `write_text`, so replacing the atomic save with a direct one left
        the whole suite passing. Here the rename is made to fail: with an
        atomic save the previous layout is untouched, and with a direct
        write it has already been overwritten by the time anything fails.

        A workspace half-written by an interrupted save is worse than one
        not saved at all -- the user loses the arrangement *and* gets no
        error, because the file exists.
        """
        path = tmp_path / "work.json"
        save_layout(path, "first", LAYOUT)

        def explode(self: Path, target: Path) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "replace", explode)
        with pytest.raises(OSError, match="disk full"):
            save_layout(path, "second", (_component("c", 200, 200),))

        survivor = load_layout(path)
        assert survivor.name == "first"
        assert survivor.components == LAYOUT

    def test_an_unknown_version_is_refused(self, tmp_path: Path) -> None:
        """Placement rules that changed between versions would put
        components somewhere the user never left them."""
        path = tmp_path / "future.json"
        path.write_text(
            SavedLayout(version=LAYOUT_VERSION + 1, name="x", components=LAYOUT).model_dump_json()
        )
        with pytest.raises(LayoutError, match="version"):
            load_layout(path)

    def test_a_missing_layout_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(LayoutError, match="no layout"):
            load_layout(tmp_path / "absent.json")


class TestFewerDisplays:
    def test_components_on_absent_displays_are_reported(self) -> None:
        """Reported rather than reflowed. Silently moving them to display 0
        loses the arrangement permanently: the user reconnects the monitor
        and the layout does not come back, because it was rewritten while
        the monitor was gone."""
        spread = (_component("a", 0, 0), _component("b", 0, 0, display=2))
        stranded = offscreen_components(spread, displays=1)
        assert [c.id for c in stranded] == ["b"]

    def test_nothing_is_stranded_when_the_displays_are_there(self) -> None:
        spread = (_component("a", 0, 0), _component("b", 0, 0, display=2))
        assert offscreen_components(spread, displays=3) == ()

    def test_reporting_does_not_modify_the_layout(self) -> None:
        spread = (_component("a", 0, 0), _component("b", 0, 0, display=2))
        offscreen_components(spread, displays=1)
        placement = spread[1].placement
        assert placement is not None
        assert placement.display == 2

    def test_a_machine_with_no_displays_is_refused(self) -> None:
        with pytest.raises(LayoutError, match="no displays"):
            offscreen_components(LAYOUT, displays=0)
