"""The swaption volatility surface (spec §11.3).

What is tested here is mostly what the surface *refuses* to do. Aggregating
numbers into a grid is easy and would pass a test suite that only checked
medians; the properties that make a surface honest are that it does not
interpolate, that it carries how many prints stand behind each node, and that
it says what it left out.

Run on one day of the live tape it produces 8 nodes at 14% grid coverage,
several with dispersion above 100% — meaning prints at the same point
disagreed by more than the level itself. That is not a usable market surface,
and the object says so through its own fields rather than through a caveat
somebody has to remember.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from treble.analytics.vol.surface import (
    MIN_OBSERVATIONS_FOR_CONFIDENT,
    EmptySurfaceError,
    build_surface,
)
from treble.analytics.vol.swaption import SwaptionQuote

_build = build_surface.__wrapped__
TRADED = date(2026, 7, 13)
FORWARD = 0.030


def _quote(expiry_years: float, tenor_years: float, *, strike: float = FORWARD) -> SwaptionQuote:
    expiry = TRADED + timedelta(days=round(expiry_years * 365.25))
    return SwaptionQuote(
        payer=True,
        expiry=expiry,
        underlier_maturity=expiry + timedelta(days=round(tenor_years * 365.25)),
        strike=strike,
        premium_fraction=0.01,
        currency="EUR",
        traded=TRADED,
    )


def _triples(*specs: tuple[float, float, float]) -> list[tuple[SwaptionQuote, float, float]]:
    return [(_quote(e, t), FORWARD, v) for e, t, v in specs]


class TestItDoesNotInterpolate:
    def test_an_unobserved_node_is_none_not_the_nearest(self) -> None:
        """An interpolated volatility looks exactly like an observed one on a
        screen, and this surface's whole claim is that its numbers were
        paid."""
        surface = _build(_triples((1.0, 10.0, 0.0090)), as_of=TRADED, currency="EUR")
        assert surface.at(1.0, 10.0) is not None
        assert surface.at(5.0, 10.0) is None
        assert surface.at(1.0, 30.0) is None

    def test_coverage_reports_how_much_of_the_grid_is_real(self) -> None:
        surface = _build(
            _triples((1.0, 10.0, 0.0090), (2.0, 5.0, 0.0085)), as_of=TRADED, currency="EUR"
        )
        assert 0.0 < surface.coverage < 0.10


class TestEachNodeCarriesItsEvidence:
    def test_observation_counts_travel_with_the_number(self) -> None:
        """A node backed by one print and one backed by twelve are different
        objects; a grid showing only the number makes them look alike."""
        surface = _build(
            _triples(
                (1.0, 10.0, 0.0090), (1.0, 10.0, 0.0092), (1.0, 10.0, 0.0088), (2.0, 5.0, 0.0085)
            ),
            as_of=TRADED,
            currency="EUR",
        )
        thick = surface.at(1.0, 10.0)
        thin = surface.at(2.0, 5.0)
        assert thick is not None and thin is not None
        assert thick.observations == 3
        assert thin.observations == 1
        assert thick.is_confident is True
        assert thin.is_confident is False

    def test_a_thin_node_is_marked_not_dropped(self) -> None:
        """One print is still the only thing the market said about that
        point. Dropping it would report silence where there was a trade."""
        surface = _build(_triples((1.0, 1.0, 0.0100)), as_of=TRADED, currency="EUR")
        node = surface.at(1.0, 1.0)
        assert node is not None
        assert node.is_confident is False
        assert MIN_OBSERVATIONS_FOR_CONFIDENT > 1

    def test_dispersion_shows_when_the_prints_disagreed(self) -> None:
        """Measured on the live tape, several nodes have dispersion above
        100% — the prints at one point disagreeing by more than the level.
        The median alone cannot say that."""
        surface = _build(
            _triples((1.0, 10.0, 0.0050), (1.0, 10.0, 0.0100), (1.0, 10.0, 0.0150)),
            as_of=TRADED,
            currency="EUR",
        )
        node = surface.at(1.0, 10.0)
        assert node is not None
        assert node.volatility == pytest.approx(0.0100)
        assert node.dispersion == pytest.approx(1.0)

    def test_the_median_not_the_mean(self) -> None:
        """One crossed print at a stale level moves a mean and does not move
        a median."""
        surface = _build(
            _triples((1.0, 10.0, 0.0090), (1.0, 10.0, 0.0091), (1.0, 10.0, 0.0800)),
            as_of=TRADED,
            currency="EUR",
        )
        node = surface.at(1.0, 10.0)
        assert node is not None
        assert node.volatility == pytest.approx(0.0091)


class TestItSaysWhatItLeftOut:
    def test_off_the_money_prints_are_excluded_and_counted(self) -> None:
        """Beyond the band the tape's implied vols are unexplained in both
        Black and Bachelier terms. Including them smears that across the
        grid; excluding them silently hides that the surface covers only the
        middle."""
        quotes = [
            (_quote(1.0, 10.0), FORWARD, 0.0090),
            (_quote(1.0, 10.0, strike=FORWARD * 0.667), FORWARD, 0.0330),
        ]
        surface = _build(quotes, as_of=TRADED, currency="EUR")
        assert surface.excluded_off_the_money == 1
        node = surface.at(1.0, 10.0)
        assert node is not None
        assert node.observations == 1

    def test_a_trade_far_from_any_grid_point_is_dropped_not_stretched(self) -> None:
        """A 4-year expiry labelled "5Y" is a wrong label on a real number,
        which is worse than an absent one."""
        surface = _build(
            _triples((1.0, 10.0, 0.0090), (14.0, 10.0, 0.0090)), as_of=TRADED, currency="EUR"
        )
        assert surface.excluded_no_bucket == 1
        assert len(surface.nodes) == 1

    def test_a_trade_near_a_grid_point_is_kept(self) -> None:
        """The drift allowance exists because a trade struck on a business
        day is never exactly 1.0000 years out."""
        surface = _build(_triples((1.04, 10.1, 0.0090)), as_of=TRADED, currency="EUR")
        assert surface.excluded_no_bucket == 0
        assert surface.at(1.0, 10.0) is not None

    def test_nothing_surviving_raises_with_the_counts(self) -> None:
        """ "No swaptions traded" and "a surface with no points" render the
        same and mean different things."""
        with pytest.raises(EmptySurfaceError, match="outside the"):
            _build(
                [(_quote(1.0, 10.0, strike=FORWARD * 0.5), FORWARD, 0.03)],
                as_of=TRADED,
                currency="EUR",
            )

    def test_a_zero_moneyness_band_is_refused(self) -> None:
        with pytest.raises(ValueError, match="admits nothing"):
            _build(
                _triples((1.0, 10.0, 0.0090)),
                as_of=TRADED,
                currency="EUR",
                moneyness_band=0.0,
            )

    def test_a_non_positive_forward_is_treated_as_off_the_money(self) -> None:
        """Moneyness is undefined against a zero forward; the print is
        excluded and counted rather than dividing by it."""
        with pytest.raises(EmptySurfaceError):
            _build([(_quote(1.0, 10.0), 0.0, 0.0090)], as_of=TRADED, currency="EUR")


class TestOneDayUnlessToldOtherwise:
    """Volatility moves. Pooling a fortnight at one node reports the average
    of a fortnight's surfaces as though it were today's — the same failure as
    a curve whose front end is March's and whose long end is May's.

    This was not hypothetical: the first multi-day build pooled fifteen days
    silently, raising grid coverage from 21% to 79% and median node
    dispersion from under 20% to 88%, with nodes at 376%. The coverage was
    real and the agreement was not.
    """

    def test_prints_from_another_day_are_refused(self) -> None:
        other = SwaptionQuote(
            payer=True,
            expiry=TRADED + timedelta(days=365),
            underlier_maturity=TRADED + timedelta(days=365 * 11),
            strike=FORWARD,
            premium_fraction=0.01,
            currency="EUR",
            traded=TRADED - timedelta(days=18),
        )
        with pytest.raises(ValueError, match="trading days"):
            _build(
                [(_quote(1.0, 10.0), FORWARD, 0.0090), (other, FORWARD, 0.0300)],
                as_of=TRADED,
                currency="EUR",
            )

    def test_pooling_is_allowed_when_asked_for_and_recorded(self) -> None:
        """Deliberate is fine; silent is not. The span travels on the result
        so nothing downstream can read a fortnight's average as one day."""
        other = SwaptionQuote(
            payer=True,
            expiry=TRADED + timedelta(days=365),
            underlier_maturity=TRADED + timedelta(days=365 * 11),
            strike=FORWARD,
            premium_fraction=0.01,
            currency="EUR",
            traded=TRADED - timedelta(days=18),
        )
        surface = _build(
            [(_quote(1.0, 10.0), FORWARD, 0.0090), (other, FORWARD, 0.0300)],
            as_of=TRADED,
            currency="EUR",
            pool_days=True,
        )
        assert surface.pooled_days == 2

    def test_a_single_day_surface_says_it_spans_one(self) -> None:
        surface = _build(_triples((1.0, 10.0, 0.0090)), as_of=TRADED, currency="EUR")
        assert surface.pooled_days == 1
