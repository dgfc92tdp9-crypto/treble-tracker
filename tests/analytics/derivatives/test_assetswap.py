"""Par-par asset swaps (spec §12.1).

The anchor is the case where the answer must be exactly zero: a bond at par
whose coupon equals the swap rate has nothing to say over the index. Both
terms vanish independently there, so it pins each of them rather than their
sum — which matters, because a sign error in one would otherwise hide inside
a total that happened to look reasonable.
"""

from __future__ import annotations

import pytest

from treble.analytics.derivatives.assetswap import asset_swap_spread

_asw = asset_swap_spread.__wrapped__  # type: ignore[attr-defined]

ANNUITY = 8.5
SWAP_RATE = 0.0325


class TestTheAnchor:
    def test_a_par_bond_at_the_swap_rate_has_no_spread(self) -> None:
        """Nothing to say over the index. Both terms are zero here, so this
        pins each rather than a sum that happens to cancel."""
        priced = _asw(price=100.0, coupon=SWAP_RATE, swap_rate=SWAP_RATE, annuity=ANNUITY)
        assert priced.spread_bp == pytest.approx(0.0)
        assert priced.price_bp == pytest.approx(0.0)
        assert priced.coupon_bp == pytest.approx(0.0)

    def test_the_terms_sum_to_the_spread(self) -> None:
        priced = _asw(price=94.0, coupon=0.045, swap_rate=SWAP_RATE, annuity=ANNUITY)
        assert priced.price_bp + priced.coupon_bp == pytest.approx(priced.spread_bp)


class TestEachTermSeparately:
    def test_a_discount_bond_widens_the_spread_through_price_alone(self) -> None:
        """Coupon held at the swap rate, so only the price term can move.
        A cheap bond pays more over the index."""
        priced = _asw(price=94.0, coupon=SWAP_RATE, swap_rate=SWAP_RATE, annuity=ANNUITY)
        assert priced.coupon_bp == pytest.approx(0.0)
        assert priced.price_bp > 0.0
        # 6 points of discount over an 8.5 annuity is ~70bp a year.
        assert priced.spread_bp == pytest.approx(6.0 / ANNUITY / 100.0 * 1e4, rel=1e-9)

    def test_a_premium_bond_tightens_the_spread(self) -> None:
        priced = _asw(price=106.0, coupon=SWAP_RATE, swap_rate=SWAP_RATE, annuity=ANNUITY)
        assert priced.spread_bp < 0.0

    def test_a_high_coupon_widens_it_through_the_coupon_term_alone(self) -> None:
        """Price held at par, so only the coupon term can move."""
        priced = _asw(price=100.0, coupon=0.0425, swap_rate=SWAP_RATE, annuity=ANNUITY)
        assert priced.price_bp == pytest.approx(0.0)
        assert priced.coupon_bp == pytest.approx(100.0)

    def test_a_longer_annuity_dilutes_the_price_term_but_not_the_coupon(self) -> None:
        """The price difference is amortised over the swap's life; the
        coupon difference is paid every period regardless. A formula that
        divided both would make a long bond's coupon advantage vanish."""
        short = _asw(price=94.0, coupon=0.0425, swap_rate=SWAP_RATE, annuity=4.0)
        long = _asw(price=94.0, coupon=0.0425, swap_rate=SWAP_RATE, annuity=12.0)
        assert short.price_bp > long.price_bp
        assert short.coupon_bp == pytest.approx(long.coupon_bp)


class TestItSaysWhichTermDominates:
    def test_a_deeply_discounted_par_coupon_bond_is_price_dominated(self) -> None:
        priced = _asw(price=80.0, coupon=SWAP_RATE, swap_rate=SWAP_RATE, annuity=ANNUITY)
        assert priced.dominated_by_price is True

    def test_a_par_bond_with_a_rich_coupon_is_coupon_dominated(self) -> None:
        priced = _asw(price=100.0, coupon=0.06, swap_rate=SWAP_RATE, annuity=ANNUITY)
        assert priced.dominated_by_price is False


class TestItRefusesBadInput:
    def test_a_zero_annuity_is_refused(self) -> None:
        """Dividing by it would return infinity, which renders as a very
        wide spread rather than as the error it is."""
        with pytest.raises(ValueError, match="cannot amortise"):
            _asw(price=98.0, coupon=SWAP_RATE, swap_rate=SWAP_RATE, annuity=0.0)

    def test_a_non_positive_price_is_refused(self) -> None:
        with pytest.raises(ValueError, match="data error"):
            _asw(price=0.0, coupon=SWAP_RATE, swap_rate=SWAP_RATE, annuity=ANNUITY)
