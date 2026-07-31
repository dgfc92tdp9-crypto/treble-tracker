"""Screens must state the period a figure covers (audit Finding 10).

IBM files both a half-year (3,381,000,000) and a quarter (2,165,000,000) of
net income ending 2026-06-30. TAPI shows the quarter. Unlabelled, that reads
as an annual figure five times larger than it is — a plausible number
meaning something other than what a reader takes it to mean, which is this
project's own stated failure mode.
"""

from __future__ import annotations

from datetime import date

import pytest

from treble.tapi.types import FieldResult


class TestPeriodLabel:
    def test_a_quarter_reads_as_months_to_a_date(self) -> None:
        result = FieldResult(
            value=1, effective_from=date(2026, 4, 1), effective_to=date(2026, 6, 30)
        )
        assert result.period_label == "3 months to 2026-06-30"

    def test_a_year_reads_as_twelve_months(self) -> None:
        result = FieldResult(
            value=1, effective_from=date(2025, 1, 1), effective_to=date(2025, 12, 31)
        )
        assert result.period_label == "12 months to 2025-12-31"

    def test_a_half_year_is_distinguishable_from_a_quarter(self) -> None:
        """The two IBM figures that started this. If these ever render the
        same, the ambiguity is back."""
        quarter = FieldResult(
            value=1, effective_from=date(2026, 4, 1), effective_to=date(2026, 6, 30)
        )
        half = FieldResult(value=1, effective_from=date(2026, 1, 1), effective_to=date(2026, 6, 30))
        assert quarter.period_label != half.period_label
        assert half.period_label == "6 months to 2026-06-30"

    def test_an_instant_reads_as_at_a_date(self) -> None:
        """A balance sheet is a position, not a flow, and must not claim to
        cover a span."""
        result = FieldResult(
            value=1, effective_from=date(2025, 12, 31), effective_to=date(2025, 12, 31)
        )
        assert result.period_label == "at 2025-12-31"

    def test_an_unknown_period_is_none_not_a_guess(self) -> None:
        assert FieldResult(value=None).period_label is None

    @pytest.mark.parametrize(
        ("start", "end", "expected"),
        [
            (date(2026, 1, 1), date(2026, 3, 31), "3 months to 2026-03-31"),
            (date(2025, 7, 1), date(2026, 6, 30), "12 months to 2026-06-30"),
            (date(2026, 6, 1), date(2026, 6, 30), "1 months to 2026-06-30"),
        ],
    )
    def test_month_counts(self, start: date, end: date, expected: str) -> None:
        assert FieldResult(value=1, effective_from=start, effective_to=end).period_label == expected
