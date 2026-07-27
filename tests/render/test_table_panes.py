"""Table panes must actually draw (spec §6.1).

MDL, FLDS and SPTR are tables. When `table_scroll` had no drawing they
rendered as an empty box while every other check passed: the data resolved
correctly, and conformance was satisfied because it asserts a pane's region
and binding and never its pixels. The screen was right and the user saw
nothing.

That combination — correct data, green suite, blank screen — is the reason
these exist.
"""

from __future__ import annotations

from treble.render.contract.buffer import ResolvedPane
from treble.render.contract.schema import PaneType, Rect
from treble.render.tui.renderer import table_lines

REGION = Rect(row=0, col=0, height=6, width=60)


def pane(rows: tuple[tuple[str | float | int | None, ...], ...]) -> ResolvedPane:
    return ResolvedPane(
        region=REGION, pane_type=PaneType.TABLE_SCROLL, binding="sys:models", data=rows
    )


class TestTableIsDrawn:
    def test_cell_values_appear(self) -> None:
        lines = table_lines(
            pane((("bonds.dv01", "1.0", "§10.1", "Price value of a bp"),)), height=6, width=60
        )
        assert "bonds.dv01" in lines[0]
        assert "§10.1" in lines[0]

    def test_columns_align(self) -> None:
        lines = table_lines(pane((("a", "1"), ("longer-identifier", "2"))), height=6, width=60)
        # The second column starts at the same offset on both rows, which
        # is the whole point of a table rather than joined strings.
        assert lines[0].index("1") == lines[1].index("2")

    def test_empty_data_says_so_rather_than_drawing_nothing(self) -> None:
        lines = table_lines(pane(()), height=6, width=60)
        assert "no rows" in lines[0]

    def test_none_renders_as_blank_not_the_word_none(self) -> None:
        lines = table_lines(pane((("x", None),)), height=6, width=60)
        assert "None" not in lines[0]


class TestTruncationIsAnnounced:
    def test_overflow_is_reported(self) -> None:
        """A table silently cut off is a wrong display, not a tidy one."""
        rows = tuple((f"row{i}", str(i)) for i in range(20))
        lines = table_lines(pane(rows), height=6, width=60)
        assert len(lines) <= 6
        assert "more rows" in lines[-1]

    def test_no_footer_when_everything_fits(self) -> None:
        rows = tuple((f"row{i}", str(i)) for i in range(3))
        lines = table_lines(pane(rows), height=6, width=60)
        assert not any("more rows" in line for line in lines)

    def test_lines_never_exceed_the_region(self) -> None:
        rows = ((("x" * 200), ("y" * 200)),)
        for line in table_lines(pane(rows), height=6, width=40):
            assert len(line) <= 40
