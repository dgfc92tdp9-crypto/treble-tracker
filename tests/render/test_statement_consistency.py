"""A screen that shows figures which do not add up must say so.

`period_from` answers "did this number come from the period the heading
claims". The identities in `core.consistency` answer the question it cannot:
**do the numbers agree with each other**, which catches a wrong value even
when every period lines up.

These test the *wiring*, deliberately. The identities have their own tests
in `tests/core/test_consistency.py`, and the last time this repository
verified a rule thoroughly and its wiring not at all, mutation testing found
that deleting the call killed nothing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from treble.core.consistency import ASSETS, BALANCE_TOTAL, LIABILITIES
from treble.core.identifiers import SecurityQuery, YellowKey
from treble.render.contract.registry import get_screen
from treble.render.contract.resolver import ScreenContext, resolve
from treble.render.contract.schema import Attr
from treble.tapi.types import FieldResult

APPLE = SecurityQuery(ticker="AAPL", key=YellowKey.EQUITY)
AS_OF = datetime(2026, 9, 1, tzinfo=UTC)
PERIOD_END = date(2026, 3, 28)

#: Apple's filed balance sheet. Assets equal the filer's own total, which is
#: what the strongest identity compares.
CONSISTENT = {
    ASSETS: 371_082_000_000.0,
    BALANCE_TOTAL: 371_082_000_000.0,
    LIABILITIES: 264_591_000_000.0,
}
#: The same statement with a total that cannot be right.
BROKEN = {**CONSISTENT, BALANCE_TOTAL: 250_000_000_000.0}


class Stub:
    def __init__(self, values: dict[str, float]) -> None:
        self.values = values

    def field(
        self,
        security: SecurityQuery | None,
        mnemonic: str,
        overrides: dict[str, str],
        *,
        as_of: datetime,
    ) -> FieldResult:
        return FieldResult(
            value=self.values.get(mnemonic),
            effective_from=PERIOD_END,
            effective_to=PERIOD_END,
        )

    def series(self, *args: object, **kwargs: object) -> list[list[object]]:
        return []


def _render(values: dict[str, float]):
    return resolve(
        get_screen("FA"),
        ScreenContext(security=APPLE, tab="balance"),
        as_of=AS_OF,
        tapi=Stub(values),
    )


class TestAStatementThatDoesNotFoot:
    def test_it_is_footnoted(self) -> None:
        buffer = _render(BROKEN)
        assert len(buffer.footnotes) == 1
        assert "do not reconcile" in buffer.footnotes[0]

    def test_the_footnote_carries_the_arithmetic(self) -> None:
        """ "These figures do not reconcile" is an accusation. The numbers
        that make it are what lets someone check."""
        (note,) = _render(BROKEN).footnotes
        assert "371,082,000,000" in note and "250,000,000,000" in note

    def test_it_says_the_values_are_as_filed(self) -> None:
        """A violation does not mean the store is wrong. It records what the
        filer said, and the disagreement is between those statements — so
        the note must not read as "our data is broken"."""
        (note,) = _render(BROKEN).footnotes
        assert "as filed" in note

    def test_the_cells_involved_are_marked(self) -> None:
        """A footnote below a screen of two hundred numbers does not tell
        anyone *which* two hundred are in question."""
        marked = [c for c in _render(BROKEN).cells if Attr.WARNING in c.attrs]
        assert len(marked) == 2

    def test_uninvolved_cells_are_not_marked(self) -> None:
        """Liabilities is in the statement and in no failing identity here.
        Marking the whole screen would make the mark meaningless."""
        buffer = _render(BROKEN)
        assert sum(1 for c in buffer.cells if Attr.WARNING in c.attrs) < len(buffer.cells)


class TestAGoodStatement:
    def test_it_is_not_footnoted(self) -> None:
        """Proves every assertion above turns on the broken total rather
        than the screen footnoting unconditionally."""
        assert _render(CONSISTENT).footnotes == ()

    def test_nothing_is_marked(self) -> None:
        assert not [c for c in _render(CONSISTENT).cells if Attr.WARNING in c.attrs]

    def test_the_real_figures_are_still_displayed(self) -> None:
        """The check must not cost the screen its content."""
        from treble.render.contract.buffer import text_snapshot

        assert "371,082,000,000" in text_snapshot(_render(CONSISTENT))


class TestTheCheckCanFireAtAll:
    """The identity is worthless if no screen binds its inputs.

    `FA`'s balance tab did not bind `LiabilitiesAndStockholdersEquity` when
    the check was first wired, so the strongest identity — 0.05% break rate
    across 35,751 real filings — had nothing to compare and could never
    fire. Wired, inert, and green.
    """

    @pytest.mark.parametrize("field", [ASSETS, BALANCE_TOTAL])
    def test_the_balance_sheet_binds_both_sides_of_the_identity(self, field: str) -> None:
        tab = next(t for t in get_screen("FA").tabs if t.name == "balance")
        bound = {c.field for c in tab.cells if getattr(c, "kind", None) == "bound"}
        assert field in bound
