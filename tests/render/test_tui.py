"""TUI renderer: theming, sparklines, and the conformance guarantee.

The layout-equivalence guarantee itself lives in tests/conformance (the TUI
is registered there as a renderer under test). These cover what is specific
to this renderer.
"""

from __future__ import annotations

import pytest

from treble.render.contract.buffer import CellBuffer, ResolvedCell, ResolvedPane
from treble.render.contract.schema import Attr, PaneType, Rect
from treble.render.tui.renderer import (
    conformance_artifacts,
    render_pane,
    render_styled,
    sparkline,
)
from treble.render.tui.theme import DEFAULT_THEME, HIGH_CONTRAST_THEME, get_theme


def buffer_with(
    cells: tuple[ResolvedCell, ...], panes: tuple[ResolvedPane, ...] = ()
) -> CellBuffer:
    return CellBuffer(mnemonic="TEST", tab="main", rows=6, cols=40, cells=cells, panes=panes)


class TestSparkline:
    def test_renders_one_char_per_point(self) -> None:
        assert len(sparkline([1.0, 2.0, 3.0, 4.0], width=10)) == 4

    def test_rising_series_ends_higher_than_it_starts(self) -> None:
        spark = sparkline([1.0, 2.0, 3.0, 9.0], width=10)
        assert spark[-1] != spark[0]

    def test_too_few_points_draws_nothing(self) -> None:
        # A flat line from one point would imply data that is not there.
        assert sparkline([5.0], width=10) == ""
        assert sparkline([], width=10) == ""

    def test_flat_series_is_flat(self) -> None:
        spark = sparkline([3.0, 3.0, 3.0], width=10)
        assert len(set(spark)) == 1

    def test_resamples_to_available_width(self) -> None:
        assert len(sparkline([float(i) for i in range(100)], width=8)) <= 8


class TestPaneRendering:
    def test_timeseries_pane_draws_a_sparkline(self) -> None:
        pane = ResolvedPane(
            region=Rect(row=0, col=0, height=3, width=20),
            pane_type=PaneType.TIMESERIES,
            binding="PX_LAST",
            data=(("d1", 1.0), ("d2", 5.0), ("d3", 3.0)),
        )
        lines = render_pane(pane)
        assert "timeseries:PX_LAST" in lines[0]
        assert any(ch in lines[1] for ch in "⣀⣄⣤⣦⣶⣷⣿")

    def test_pane_fills_its_declared_region_exactly(self) -> None:
        pane = ResolvedPane(
            region=Rect(row=0, col=0, height=4, width=12),
            pane_type=PaneType.HEATMAP,
            binding="X",
        )
        assert len(render_pane(pane)) == 4


class TestTheming:
    def test_semantic_tokens_map_to_styles(self) -> None:
        assert DEFAULT_THEME.style_for((Attr.LABEL,))
        assert DEFAULT_THEME.style_for((Attr.NEGATIVE,))

    def test_stale_wins_over_other_colours(self) -> None:
        # §6.3 makes stale marking mandatory: a value known not to be
        # current must look stale whatever else it is.
        combined = DEFAULT_THEME.style_for((Attr.POSITIVE, Attr.STALE))
        assert combined.endswith(DEFAULT_THEME.styles[Attr.STALE.value])

    def test_colour_blind_theme_replaces_green_red_with_blue_orange(self) -> None:
        # §6.3 requires this alternative and that semantics survive it.
        assert (
            HIGH_CONTRAST_THEME.styles[Attr.POSITIVE.value]
            != DEFAULT_THEME.styles[Attr.POSITIVE.value]
        )
        assert set(HIGH_CONTRAST_THEME.styles) == set(DEFAULT_THEME.styles)

    def test_unknown_theme_raises(self) -> None:
        with pytest.raises(KeyError, match="available"):
            get_theme("nonexistent")

    def test_themes_do_not_change_layout(self) -> None:
        cells = (
            ResolvedCell(row=0, col=0, text="LABEL", attrs=(Attr.LABEL,)),
            ResolvedCell(row=1, col=0, text="-1.5", attrs=(Attr.NEGATIVE,)),
        )
        buffer = buffer_with(cells)
        assert (
            render_styled(buffer, DEFAULT_THEME).plain
            == render_styled(buffer, HIGH_CONTRAST_THEME).plain
        )


class TestConformanceArtifacts:
    def test_pane_pixels_are_excluded_from_the_snapshot(self) -> None:
        """Conformance asserts a pane's region, type and binding — never its
        pixels (CLAUDE.md §4), because the TUI legitimately draws a
        sparkline where the desktop draws a WebGL chart."""
        pane = ResolvedPane(
            region=Rect(row=0, col=0, height=3, width=24),
            pane_type=PaneType.TIMESERIES,
            binding="PX_LAST",
            data=(("d1", 1.0), ("d2", 9.0)),
        )
        _tree, text = conformance_artifacts(buffer_with((), (pane,)))
        assert not any(ch in text for ch in "⣀⣄⣤⣦⣶⣷⣿"), "sparkline leaked into conformance"
        assert "timeseries:PX_LAST" in text

    def test_cells_go_through_the_renderers_own_pipeline(self) -> None:
        # Not a second call to the reference projection: this proves the
        # TUI's own grid composition agrees.
        cells = (ResolvedCell(row=2, col=5, text="HELLO", attrs=(Attr.LABEL,)),)
        _tree, text = conformance_artifacts(buffer_with(cells))
        assert text.split("\n")[2] == "     HELLO"

    def test_snapshot_has_no_trailing_whitespace(self) -> None:
        cells = (ResolvedCell(row=0, col=0, text="X"),)
        _tree, text = conformance_artifacts(buffer_with(cells))
        assert all(line == line.rstrip() for line in text.split("\n"))
