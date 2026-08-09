"""Layout authoring through `CNVS` (spec §5.3, §4.2).

`render/layout.py` sat built, tested and unreachable for a fortnight behind
a note saying its caller was the desktop shell's drag gestures. It was not:
the caller is a command, and once that was true both renderers got layout
authoring at the same moment rather than the terminal going without.

What is tested here is the wiring and the refusals — `layout.py`'s own
suite covers the geometry. The refusals matter more than the moves: every
one of them exists because the alternative silently destroys an
arrangement, and a user who loses a workspace gets no error to report.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from treble.render.authoring import apply_layout_command
from treble.render.canvas import Canvas, CanvasComponent, Channel, Fdc3Instrument, Placement


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


def _run(canvas: Canvas | None, *args: str, data_dir: Path) -> tuple[Canvas | None, str]:
    outcome = apply_layout_command(canvas, args, data_dir=data_dir)
    return outcome.canvas, outcome.status


class TestMovingAndResizing:
    def test_a_move_lands_where_it_was_told(self, tmp_path: Path) -> None:
        moved, status = _run(_canvas(), "MOVE", "DES1", "0", "20", data_dir=tmp_path)
        assert moved is not None
        assert moved.component("DES1").placement == Placement(x=0, y=20, width=40, height=10)
        assert "(0, 20)" in status

    def test_the_component_that_did_not_move_is_untouched(self, tmp_path: Path) -> None:
        """A rebuild that dropped or shifted the others would be invisible
        on a two-component canvas unless this is asserted."""
        moved, _ = _run(_canvas(), "MOVE", "DES1", "0", "20", data_dir=tmp_path)
        assert moved is not None
        assert moved.component("YAS1").placement == Placement(x=40, y=0, width=40, height=10)

    def test_a_move_onto_another_component_is_refused_and_changes_nothing(
        self, tmp_path: Path
    ) -> None:
        """The refusal must not be advisory. Returning the moved canvas with
        a warning would leave two components stacked, and the one underneath
        is simply gone as far as the user can tell."""
        canvas, status = _run(_canvas(), "MOVE", "DES1", "40", "0", data_dir=tmp_path)
        assert canvas is None
        assert "overlap" in status.lower()

    def test_a_resize_below_the_minimum_is_refused(self, tmp_path: Path) -> None:
        canvas, status = _run(_canvas(), "SIZE", "DES1", "4", "2", data_dir=tmp_path)
        assert canvas is None
        assert "minimum" in status

    def test_a_component_with_no_placement_says_so(self, tmp_path: Path) -> None:
        """The TUI's components have no placement at all. "Not on a canvas"
        and "not found" are different mistakes and lead to different fixes."""
        canvas = Canvas([CanvasComponent(id="DES1", screen="DES")])
        result, status = _run(canvas, "MOVE", "DES1", "1", "1", data_dir=tmp_path)
        assert result is None
        assert "no placement" in status

    def test_an_unknown_component_names_what_was_asked_for(self, tmp_path: Path) -> None:
        result, status = _run(_canvas(), "MOVE", "NOPE", "1", "1", data_dir=tmp_path)
        assert result is None
        assert "NOPE" in status

    def test_coordinates_must_be_whole_cells(self, tmp_path: Path) -> None:
        """Cells, not pixels — a layout in pixels only reconstructs on the
        display it was saved from, and §5.3 has layouts follow a user."""
        result, status = _run(_canvas(), "MOVE", "DES1", "0.5", "3", data_dir=tmp_path)
        assert result is None
        assert "whole numbers" in status


class TestLinkStateSurvivesAMove:
    """The defect this is here to catch is invisible on screen: a rebuilt
    canvas that dropped its context would look right and stop following the
    group, which reads as an FDC3 fault rather than a layout one."""

    def test_the_channel_context_survives(self, tmp_path: Path) -> None:
        canvas = _canvas()
        canvas.broadcast("DES1", Fdc3Instrument(ticker="IBM"))
        moved, _ = _run(canvas, "MOVE", "DES1", "0", "20", data_dir=tmp_path)
        assert moved is not None
        context = moved.channel_context(Channel.BLUE)
        assert context is not None
        assert context.ticker == "IBM"

    def test_a_component_still_shows_what_it_was_showing(self, tmp_path: Path) -> None:
        canvas = _canvas()
        canvas.broadcast("DES1", Fdc3Instrument(ticker="IBM"))
        moved, _ = _run(canvas, "MOVE", "DES1", "0", "20", data_dir=tmp_path)
        assert moved is not None
        shown = moved.context_of("YAS1")
        assert shown is not None
        assert shown.ticker == "IBM"

    def test_an_unlinked_component_keeps_its_last_context(self, tmp_path: Path) -> None:
        """An unlinked component holds the last context it received. That
        state lives nowhere in the component list, so a rebuild that
        re-derived context from components alone would silently blank it."""
        canvas = _canvas()
        canvas.broadcast("DES1", Fdc3Instrument(ticker="IBM"))
        canvas.leave("YAS1")
        moved, _ = _run(canvas, "MOVE", "DES1", "0", "20", data_dir=tmp_path)
        assert moved is not None
        held = moved.context_of("YAS1")
        assert held is not None
        assert held.ticker == "IBM"


class TestSaveAndLoad:
    def test_a_saved_layout_comes_back(self, tmp_path: Path) -> None:
        _, status = _run(_canvas(), "SAVE", "desk", data_dir=tmp_path)
        assert "2 placed components" in status
        loaded, status = _run(_canvas(), "LOAD", "desk", data_dir=tmp_path)
        assert loaded is not None
        assert loaded.component_ids == ("DES1", "YAS1")
        assert loaded.component("YAS1").placement == Placement(x=40, y=0, width=40, height=10)

    def test_a_move_then_a_save_persists_the_move(self, tmp_path: Path) -> None:
        """End to end: the point of authoring is that the arrangement
        outlives the session."""
        moved, _ = _run(_canvas(), "MOVE", "DES1", "0", "20", data_dir=tmp_path)
        _run(moved, "SAVE", "desk", data_dir=tmp_path)
        loaded, _ = _run(_canvas(), "LOAD", "desk", data_dir=tmp_path)
        assert loaded is not None
        assert loaded.component("DES1").placement == Placement(x=0, y=20, width=40, height=10)

    def test_saving_a_canvas_with_no_placements_is_refused(self, tmp_path: Path) -> None:
        """Overwriting a good layout with one that restores to nothing is
        the worst outcome available here, and it would report success."""
        canvas = Canvas([CanvasComponent(id="DES1", screen="DES")])
        result, status = _run(canvas, "SAVE", "desk", data_dir=tmp_path)
        assert result is None
        assert "nothing on this canvas has a placement" in status

    def test_a_name_with_a_separator_is_refused_not_sanitised(self, tmp_path: Path) -> None:
        """Rewriting the name means SAVE and LOAD disagree about where the
        file went, and the layout is lost with no error at either end."""
        result, status = _run(_canvas(), "SAVE", "../desk", data_dir=tmp_path)
        assert result is None
        assert "path separator" in status

    def test_loading_a_layout_that_is_not_there_says_so(self, tmp_path: Path) -> None:
        result, status = _run(_canvas(), "LOAD", "nope", data_dir=tmp_path)
        assert result is None
        assert "no layout at" in status

    def test_a_layout_from_a_future_version_is_refused(self, tmp_path: Path) -> None:
        """Placement rules that changed between versions would put
        components somewhere the user never left them."""
        (tmp_path / "layouts").mkdir()
        (tmp_path / "layouts" / "future.json").write_text(
            '{"version": 99, "name": "future", "components": []}'
        )
        result, status = _run(_canvas(), "LOAD", "future", data_dir=tmp_path)
        assert result is None
        assert "version 99" in status


class TestTheRefusalsAreUsable:
    def test_an_unknown_verb_lists_the_ones_that_exist(self, tmp_path: Path) -> None:
        result, status = _run(_canvas(), "WIGGLE", "DES1", data_dir=tmp_path)
        assert result is None
        assert "MOVE" in status and "SAVE" in status

    @pytest.mark.parametrize(
        ("args", "expected"),
        [
            (("MOVE", "DES1"), "usage: CNVS MOVE"),
            (("SIZE", "DES1", "40"), "usage: CNVS SIZE"),
            (("SAVE",), "usage: CNVS SAVE"),
            (("LOAD", "a", "b"), "usage: CNVS LOAD"),
        ],
    )
    def test_the_wrong_number_of_arguments_shows_the_usage(
        self, args: tuple[str, ...], expected: str, tmp_path: Path
    ) -> None:
        result, status = _run(_canvas(), *args, data_dir=tmp_path)
        assert result is None
        assert expected in status

    def test_no_canvas_at_all_is_its_own_answer(self, tmp_path: Path) -> None:
        """Distinct from an empty canvas, exactly as `CNVS` itself
        distinguishes them."""
        result, status = _run(None, "MOVE", "DES1", "1", "1", data_dir=tmp_path)
        assert result is None
        assert "no canvas configured" in status

    def test_a_verb_is_accepted_in_lower_case(self, tmp_path: Path) -> None:
        """Commands are upper-case by convention but nobody types that way
        under pressure."""
        moved, _ = _run(_canvas(), "move", "DES1", "0", "20", data_dir=tmp_path)
        assert moved is not None
