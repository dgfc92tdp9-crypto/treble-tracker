"""A value from one period may not be shown under another period's heading.

Found on the live store, 2026-09-01. `DES` and `FA` for AAPL rendered:

    INCOME (as reported, USD)         3 months to 2026-03-28
    Revenue       62,900,000,000
    Net income    29,578,000,000

The net income is right. The revenue is Apple's **Q4 FY2018** figure —
`us-gaap:Revenues:USD`, a tag Apple stopped using in 2018 when it moved to
ASC 606 and `RevenueFromContractWithCustomerExcludingAssessedTax`. Nothing
was corrupt: the binding asked for the latest value of a tag, and the
latest value of that tag is seven years old.

Presented together the two implied a 47% net margin for a company that
runs about 26%, and the heading asserted a period only one of them had.

The store held the right number the whole time (111,184,000,000 for that
quarter, under the ASC 606 tag). Choosing between tags per filer is §14.1
standardisation and is not built — but showing the wrong one as though it
were current is a separate bug, and this is the guard against it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from treble.core.identifiers import SecurityQuery, YellowKey
from treble.render.contract.buffer import text_snapshot
from treble.render.contract.resolver import ScreenContext, _missing, resolve
from treble.render.contract.schema import ScreenDef
from treble.tapi.types import FieldResult

AS_OF = datetime(2026, 9, 1, tzinfo=UTC)
APPLE = SecurityQuery(ticker="AAPL", key=YellowKey.EQUITY)

#: The two facts exactly as the live store held them.
CURRENT_QUARTER = (date(2025, 12, 28), date(2026, 3, 28))
ABANDONED_TAG_PERIOD = (date(2018, 7, 1), date(2018, 9, 29))

RESPONSES = {
    "us-gaap:NetIncomeLoss:USD": FieldResult(
        value=29_578_000_000.0,
        effective_from=CURRENT_QUARTER[0],
        effective_to=CURRENT_QUARTER[1],
    ),
    "us-gaap:Revenues:USD": FieldResult(
        value=62_900_000_000.0,
        effective_from=ABANDONED_TAG_PERIOD[0],
        effective_to=ABANDONED_TAG_PERIOD[1],
    ),
}


class StubTapi:
    def field(
        self,
        security: SecurityQuery | None,
        mnemonic: str,
        overrides: dict[str, str],
        *,
        as_of: datetime,
    ) -> FieldResult:
        return RESPONSES[mnemonic]

    def series(self, *args: object, **kwargs: object) -> list[list[object]]:
        return []


def _screen(*, guarded: bool) -> ScreenDef:
    revenue: dict[str, object] = {
        "kind": "bound",
        "at": {"row": 1, "col": 14},
        "field": "us-gaap:Revenues:USD",
        "format": "{:,.0f}",
        "width": 20,
    }
    if guarded:
        revenue["period_from"] = "us-gaap:NetIncomeLoss:USD"
    return ScreenDef.model_validate(
        {
            "mnemonic": "TEST",
            "title": "Period agreement",
            "rows": 4,
            "cols": 60,
            "tabs": [
                {
                    "name": "income",
                    "cells": [
                        {
                            "kind": "period",
                            "at": {"row": 0, "col": 0},
                            "field": "us-gaap:NetIncomeLoss:USD",
                        },
                        revenue,
                        {
                            "kind": "bound",
                            "at": {"row": 2, "col": 14},
                            "field": "us-gaap:NetIncomeLoss:USD",
                            "format": "{:,.0f}",
                            "width": 20,
                        },
                    ],
                }
            ],
        }
    )


def _render(*, guarded: bool) -> str:
    buffer = resolve(
        _screen(guarded=guarded),
        ScreenContext(security=APPLE),
        as_of=AS_OF,
        tapi=StubTapi(),
    )
    return text_snapshot(buffer)


class TestAValueFromAnotherPeriodIsNotShown:
    def test_the_out_of_period_value_is_blanked(self) -> None:
        assert "62,900,000,000" not in _render(guarded=True)

    def test_it_renders_as_a_missing_value(self) -> None:
        """`—`, the same as a genuinely absent figure — which is what it is:
        the filer reported nothing under this tag for this period."""
        assert "—" in _render(guarded=True)

    def test_the_in_period_value_is_untouched(self) -> None:
        """The guard must discriminate. One that blanked the section would
        pass the assertion above while destroying the screen."""
        assert "29,578,000,000" in _render(guarded=True)

    def test_without_the_guard_the_stale_figure_is_displayed(self) -> None:
        """Proves the guard can fail — and records the defect as it was.

        Without `period_from` the 2018 revenue renders beside the 2026 net
        income under a single "3 months to 2026-03-28" heading. If this
        ever stops holding, the guard above is passing for some other
        reason and no longer tests what it claims.
        """
        unguarded = _render(guarded=False)
        assert "62,900,000,000" in unguarded
        assert "29,578,000,000" in unguarded


class TestAMissingValueOccupiesItsColumn:
    """Found by the fix above, not by the bug it fixed.

    Blanking the cash line made `fa_cashflow` render

        Cash and equivalents, carrying val—e

    because the em dash was one character at the cell origin while the
    number it replaced was right-aligned across twenty columns — so it
    landed inside the label. Pre-existing for any null in a right-aligned
    money column, and invisible until a null appeared in one whose label
    was long enough to reach.
    """

    def test_a_right_aligned_column_right_aligns_its_dash(self) -> None:
        assert _missing("{:>20,.0f}", 20) == "—".rjust(20)

    def test_a_left_aligned_column_is_unchanged(self) -> None:
        """DES places short labels beside narrow cells and reads correctly
        with a bare dash; padding those would move the mark away from the
        label it belongs to."""
        assert _missing("{:,.0f}", 20) == "—"

    def test_no_label_text_survives_inside_the_cell(self) -> None:
        """The defect as it actually appeared, stated as the property.

        `fa.screen.yaml` puts a 36-character label in the 34 columns before
        this cell, so the last two characters were always overwritten — by
        the number, and now by the dash's padding. That truncation is a
        layout question for the definition and is not what broke.

        What broke is that a *one-character* dash blanked none of those
        columns, so the label's tail re-emerged to its right and read as
        part of the value: `carrying val—e`. A missing value must occupy
        exactly the columns its value would have.
        """
        label = "Cash and equivalents, carrying value"
        screen = ScreenDef.model_validate(
            {
                "mnemonic": "TEST",
                "title": "Missing value alignment",
                "rows": 2,
                "cols": 60,
                "tabs": [
                    {
                        "name": "t",
                        "cells": [
                            {"kind": "static", "at": {"row": 0, "col": 0}, "text": label},
                            {
                                "kind": "bound",
                                "at": {"row": 0, "col": 34},
                                "field": "us-gaap:Revenues:USD",
                                "period_from": "us-gaap:NetIncomeLoss:USD",
                                "format": "{:>20,.0f}",
                                "width": 20,
                            },
                        ],
                    }
                ],
            }
        )
        rendered = text_snapshot(
            resolve(screen, ScreenContext(security=APPLE), as_of=AS_OF, tapi=StubTapi())
        )
        line = rendered.splitlines()[0]
        assert line[34:].strip() == "—", f"label text leaked into the cell: {line[34:]!r}"
        assert line[:34] == label[:34], "the dash overwrote columns left of the cell"


class TestTheShippedScreensCarryTheGuard:
    """The guard is worth nothing unless the screens that had the bug use it."""

    def test_des_revenue_is_governed_by_the_income_period(self) -> None:
        from treble.render.contract.registry import get_screen

        cells = [
            c
            for tab in get_screen("DES").tabs
            for c in tab.cells
            if getattr(c, "field", None) == "us-gaap:Revenues:USD"
        ]
        assert cells, "DES no longer binds the revenue tag; drop this test with it"
        assert all(c.period_from == "us-gaap:NetIncomeLoss:USD" for c in cells)

    def test_every_fa_income_line_is_governed(self) -> None:
        from treble.render.contract.registry import get_screen

        income = next(t for t in get_screen("FA").tabs if t.name == "income")
        bound = [
            c
            for c in income.cells
            if getattr(c, "kind", None) == "bound"
            and getattr(c, "field", None) != "us-gaap:NetIncomeLoss:USD"
        ]
        ungoverned = [c.field for c in bound if c.period_from is None]
        assert not ungoverned, f"income lines free to come from any period: {ungoverned}"
