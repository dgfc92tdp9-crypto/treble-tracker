"""Prices implied by N-PORT holdings.

The accuracy risk here is specific: an implied price is a division, and a
division silently returns a plausible number for almost any bad input. A
par amount treated as a share count understates a bond by a factor of a
hundred and still looks like a price.
"""

from __future__ import annotations

from datetime import date

import pytest

from treble.analytics.holdings.implied_price import (
    AssetCategory,
    ImpliedMark,
    UnpriceableHoldingError,
    consensus_price,
    implied_price,
)


def _price(balance: float, val_usd: float, category: AssetCategory) -> float:
    return implied_price(balance, val_usd, category).value


class TestSingleHolding:
    def test_equity_is_priced_per_share(self) -> None:
        # 1,000 shares valued at $195,000 implies $195.00 a share.
        assert _price(1000, 195_000, AssetCategory.EQUITY) == pytest.approx(195.0)

    def test_debt_is_priced_per_hundred_par(self) -> None:
        """The real fixture line: $40,000 par valued at $38,850 is a price of
        97.125, not 0.97125. Getting this wrong is a factor-of-100 error that
        still renders as a number."""
        assert _price(40_000, 38_850, AssetCategory.DEBT) == pytest.approx(97.125)

    def test_the_two_categories_differ_by_exactly_one_hundred(self) -> None:
        """Pins the scaling rule itself, so it cannot be dropped for one
        category without this failing."""
        equity = _price(40_000, 38_850, AssetCategory.EQUITY)
        debt = _price(40_000, 38_850, AssetCategory.DEBT)
        assert debt == pytest.approx(equity * 100.0)

    def test_zero_quantity_refuses_rather_than_dividing(self) -> None:
        with pytest.raises(UnpriceableHoldingError, match="balance is zero"):
            _price(0, 38_850, AssetCategory.DEBT)

    def test_short_position_prices_from_matching_signs(self) -> None:
        # A short is a negative quantity and a negative value; the ratio is
        # still the price.
        assert _price(-1000, -195_000, AssetCategory.EQUITY) == pytest.approx(195.0)

    def test_negative_quantity_with_positive_value_is_refused(self) -> None:
        """Mismatched signs are a filing error, and the ratio would come back
        negative — a negative share price presented as fact."""
        with pytest.raises(UnpriceableHoldingError, match="negative quantity"):
            _price(-1000, 195_000, AssetCategory.EQUITY)

    def test_result_carries_a_model_envelope(self) -> None:
        """I3: every analytic output is stamped."""
        result = implied_price(1000, 195_000, AssetCategory.EQUITY)
        assert result.model_id == "holdings.implied_price"
        assert result.model_version


class TestConsensus:
    @staticmethod
    def marks(*prices: float, on: date = date(2026, 3, 31)) -> tuple[ImpliedMark, ...]:
        return tuple(ImpliedMark(price=p, filer=f"fund{i}", as_of=on) for i, p in enumerate(prices))

    def test_marks_from_different_dates_are_refused(self) -> None:
        """Found in production: five fund families filed on three different
        period ends, and combining them made the spread measure elapsed time
        as well as disagreement — 447 bp on Equinix — with nothing in the
        number saying so. A spread that conflates two causes is worse than
        no spread, because a reader would act on it."""
        mixed = (
            *self.marks(100.0, on=date(2026, 3, 31)),
            *self.marks(104.0, on=date(2026, 4, 30)),
        )
        with pytest.raises(UnpriceableHoldingError, match="span report dates"):
            consensus_price(mixed)

    def test_the_date_travels_with_the_consensus(self) -> None:
        """So a caller can say what the price is a price *as of*."""
        assert consensus_price(self.marks(100.0, 101.0)).value.as_of == date(2026, 3, 31)

    def test_median_is_used_not_the_mean(self) -> None:
        """One filer off by a factor of ten must not move the answer. A mean
        would return 224.0 here; the median returns the price the other
        filers actually agree on."""
        result = consensus_price(self.marks(100.0, 101.0, 102.0, 1000.0)).value
        assert result.price == pytest.approx(101.5)

    def test_dispersion_separates_agreement_from_scatter(self) -> None:
        tight = consensus_price(self.marks(100.0, 100.1, 100.2, 100.3)).value
        wide = consensus_price(self.marks(80.0, 95.0, 105.0, 130.0)).value
        assert tight.dispersion < wide.dispersion
        # The point of reporting it: these must not render identically.
        assert tight.dispersion_bps_of_price < wide.dispersion_bps_of_price

    def test_low_and_high_are_reported(self) -> None:
        result = consensus_price(self.marks(97.0, 98.0, 99.0, 103.0)).value
        assert (result.low, result.high) == (97.0, 103.0)

    def test_single_mark_reports_one_filer_and_no_dispersion(self) -> None:
        """Zero dispersion from one filer is not agreement, and the count is
        what tells a reader that."""
        result = consensus_price(self.marks(97.0)).value
        assert result.filers == 1
        assert result.dispersion == 0.0

    def test_filer_count_is_the_confidence_signal(self) -> None:
        assert consensus_price(self.marks(*[100.0] * 40)).value.filers == 40

    def test_no_marks_refuses(self) -> None:
        with pytest.raises(UnpriceableHoldingError, match="no marks"):
            consensus_price(())

    def test_two_marks_use_the_half_range_not_quartiles(self) -> None:
        """Quartiles of two points would imply more data than exists."""
        result = consensus_price(self.marks(98.0, 102.0)).value
        assert result.dispersion == pytest.approx(2.0)

    def test_dispersion_is_scale_free_when_expressed_in_bps(self) -> None:
        """A 1% spread on a $10 stock and on a $1,000 stock must compare."""
        cheap = consensus_price(self.marks(9.9, 9.95, 10.05, 10.1)).value
        rich = consensus_price(self.marks(990.0, 995.0, 1005.0, 1010.0)).value
        assert cheap.dispersion_bps_of_price == pytest.approx(
            rich.dispersion_bps_of_price, rel=0.05
        )
