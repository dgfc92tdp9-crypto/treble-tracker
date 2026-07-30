"""Declared pane ordering (spec §6.1).

A chart wants oldest-first; a history table wants newest-first. Without a
declared order, HP truncated an ascending series to the pane height and
showed the *oldest* thirteen observations while hiding today's — the wrong
answer, delivered tidily, with nothing failing.

Order is declared on the pane rather than inferred by a renderer, because a
renderer reordering rows by guessing what a binding "means" would be
deciding presentation from data it has no business interpreting.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from treble.core.identifiers import SecurityQuery
from treble.render.contract.resolver import ScreenContext, resolve
from treble.render.contract.schema import PaneType, ScreenDef, load_screen
from treble.tapi.types import FieldResult

AS_OF = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
SERIES = (("2026-07-01", 1.0), ("2026-07-02", 2.0), ("2026-07-03", 3.0))


class FakeTapi:
    def field(
        self,
        security: SecurityQuery | None,
        mnemonic: str,
        overrides: dict[str, str],
        *,
        as_of: datetime,
    ) -> FieldResult:
        return FieldResult(value=None)

    def series(
        self, security: SecurityQuery | None, binding: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        return SERIES


def _screen(order: str | None) -> ScreenDef:
    line = f"        order: {order}\n" if order else ""
    return load_screen(
        "mnemonic: TEST\ntitle: t\nrows: 10\ncols: 40\nnamespaces: []\n"
        "tabs:\n  - name: main\n    cells:\n"
        "      - kind: pane\n"
        "        region: {row: 0, col: 0, height: 5, width: 20}\n"
        "        pane_type: table_scroll\n"
        '        binding: "PX_LAST"\n' + line
    )


def _data(order: str | None) -> tuple[tuple[str | float | int | None, ...], ...]:
    buffer = resolve(_screen(order), ScreenContext(), as_of=AS_OF, tapi=FakeTapi())
    return buffer.panes[0].data


def test_descending_reverses_the_series() -> None:
    assert _data("desc") == tuple(reversed(SERIES))


def test_ascending_preserves_the_series() -> None:
    assert _data("asc") == SERIES


def test_ascending_is_the_default() -> None:
    """A chart is the common case, and silently reversing one would be
    worse than the table problem this feature fixes."""
    assert _data(None) == SERIES


def test_order_is_rejected_if_not_a_known_value() -> None:
    with pytest.raises(Exception, match="order"):
        _screen("sideways")


def test_pane_type_is_unaffected_by_order() -> None:
    buffer = resolve(_screen("desc"), ScreenContext(), as_of=AS_OF, tapi=FakeTapi())
    assert buffer.panes[0].pane_type is PaneType.TABLE_SCROLL
