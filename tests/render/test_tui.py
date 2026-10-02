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


class TestTheTimeseriesChart:
    """What a chart pane must guarantee, now that it draws one.

    This replaces a test asserting the pane rendered the string
    `[timeseries:PX_LAST]` — the placeholder it emitted before any chart
    existed. That test passed for as long as `GP` drew a one-row sparkline
    into a twelve-row region, because the placeholder was all it checked.
    """

    @staticmethod
    def _pane(points: list[float], *, height: int = 12, width: int = 80) -> ResolvedPane:
        return ResolvedPane(
            region=Rect(row=0, col=0, height=height, width=width),
            pane_type=PaneType.TIMESERIES,
            binding="PX_LAST",
            data=tuple((f"2020-01-{i % 28 + 1:02d}", v) for i, v in enumerate(points)),
        )

    def test_the_chart_uses_the_height_it_is_given(self) -> None:
        # The defect this whole change exists for: eleven of twelve rows
        # were blank because the renderer drew a single sparkline row.
        lines = render_pane(self._pane([float(i) for i in range(500)]))
        assert len(lines) == 12
        drawn = [line for line in lines if "█" in line]
        assert len(drawn) >= 10, f"only {len(drawn)} rows carry the series"

    def test_an_extreme_between_samples_is_not_discarded(self) -> None:
        """The reason aggregation replaced decimation.

        A single spike placed off the sampling stride. `points[::step]`
        skips it entirely — 5,017 closes into 78 columns keeps 1 in 64 —
        so the chart showed a calm series on a day the price doubled. The
        per-column min/max cannot miss it: the spike's own column spans up
        to it.
        """
        points = [10.0] * 500
        points[251] = 99.0  # deliberately not a multiple of the old stride
        lines = render_pane(self._pane(points))
        assert "99" in lines[0], "the spike is not on the axis"
        # ...and it is drawn, not merely labelled.
        assert lines[0].rstrip().endswith("█") or "█" in lines[0]

    def test_the_axis_rule_stays_in_one_column(self) -> None:
        """A label wider than its field used to bend the axis.

        `.6g` on 171.344 is seven characters in a six-character field, so
        the `│` moved one column right on exactly the rows carrying a
        label. The rule must be in the same column on every row.
        """
        lines = render_pane(self._pane([2.60714, 171.344, 340.08, 99.9, 1000.5]))
        columns = {line.index("│") for line in lines if "│" in line}
        assert len(columns) == 1, f"rule drifts across columns {sorted(columns)}"

    def test_the_time_axis_carries_the_series_endpoints(self) -> None:
        pane = self._pane([float(i) for i in range(100)])
        lines = render_pane(pane)
        assert lines[-1].lstrip().startswith("└")
        assert str(pane.data[0][0]) in lines[-1]
        assert str(pane.data[-1][0]) in lines[-1]

    def test_a_descending_series_labels_oldest_first(self) -> None:
        # Direction is read from the stamps, so a pane the resolver has
        # already reversed still labels its axis left-to-right in time.
        pane = ResolvedPane(
            region=Rect(row=0, col=0, height=6, width=40),
            pane_type=PaneType.TIMESERIES,
            binding="PX_LAST",
            data=(("2026-03-01", 3.0), ("2026-02-01", 2.0), ("2026-01-01", 1.0)),
        )
        axis = render_pane(pane)[-1]
        assert axis.index("2026-01-01") < axis.index("2026-03-01")

    def test_one_point_is_not_plotted_as_a_line(self) -> None:
        lines = render_pane(self._pane([42.0]))
        assert "not plotted" in lines[0]
        assert "█" not in "".join(lines)

    def test_a_flat_series_draws_one_row_not_a_block(self) -> None:
        lines = render_pane(self._pane([7.0] * 50))
        assert sum(1 for line in lines if "█" in line) == 1

    def test_a_one_row_pane_falls_back_to_the_sparkline(self) -> None:
        # The sparkline still has a job: at height 1 its decimation is the
        # honest best available, and the chart needs two rows minimum.
        lines = render_pane(self._pane([float(i) for i in range(50)], height=1, width=20))
        assert len(lines) == 1
        assert any(ch in lines[0] for ch in "⣀⣄⣤⣦⣶⣷⣿")

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
