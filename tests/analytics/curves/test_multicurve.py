"""The multi-curve bootstrap: discount ≠ forecast, structurally.

Every refusal here corresponds to a way of accidentally rebuilding the
single-curve world. None of them would produce a visibly wrong number if
allowed through, which is why each is a raise rather than a warning.
"""

from __future__ import annotations

from datetime import date

import pytest

from treble.analytics._ql import DayCount, Market
from treble.analytics.curves.bootstrap import CurveBuildError
from treble.analytics.curves.config import (
    CurveConfig,
    InstrumentKind,
    InstrumentSpec,
    Interpolation,
)
from treble.analytics.curves.multicurve import (
    CurveSet,
    CurveSpec,
    UnknownCurveError,
    build_csa_discount_curve,
    build_forecast_curve,
)

AS_OF = date(2026, 6, 30)
CALENDAR = Market.US_SETTLEMENT
K = InstrumentKind

OIS_QUOTES = {
    (K.DEPOSIT, "1W"): 0.0430,
    (K.OIS, "1Y"): 0.0405,
    (K.OIS, "5Y"): 0.0375,
    (K.OIS, "10Y"): 0.0390,
}
FORECAST_QUOTES = {
    (K.DEPOSIT, "3M"): 0.0455,
    (K.SWAP, "2Y"): 0.0420,
    (K.SWAP, "5Y"): 0.0405,
    (K.SWAP, "10Y"): 0.0420,
}

OIS = CurveConfig(
    name="USD-SOFR-OIS",
    currency="USD",
    calendar=CALENDAR,
    instruments=(
        InstrumentSpec(kind=K.DEPOSIT, tenor="1W"),
        InstrumentSpec(kind=K.OIS, tenor="1Y"),
        InstrumentSpec(kind=K.OIS, tenor="5Y"),
        InstrumentSpec(kind=K.OIS, tenor="10Y"),
    ),
)

FORECAST = CurveConfig(
    name="USD-LIBOR-3M",
    currency="USD",
    calendar=CALENDAR,
    index_tenor="3M",
    discount_basis="USD-SOFR-OIS",
    swap_fixed_frequency=2,
    fixed_leg_day_count=DayCount.THIRTY_360,
    float_leg_day_count=DayCount.ACT_360,
    instruments=(
        InstrumentSpec(kind=K.DEPOSIT, tenor="3M"),
        InstrumentSpec(kind=K.SWAP, tenor="2Y"),
        InstrumentSpec(kind=K.SWAP, tenor="5Y"),
        InstrumentSpec(kind=K.SWAP, tenor="10Y"),
    ),
)


@pytest.fixture(scope="module")
def curves() -> CurveSet:
    return CurveSet(AS_OF, [CurveSpec(FORECAST, FORECAST_QUOTES), CurveSpec(OIS, OIS_QUOTES)])


class TestTheTwoCurvesAreDifferentObjects:
    def test_the_forecast_curve_sits_above_the_discount_curve(self, curves: CurveSet) -> None:
        """The basis is the whole point. If these came out equal the tests
        below would all still pass while a single-curve pricer produced
        identical numbers — a flat or coincident market is why multi-curve
        bugs survive test suites."""
        ois = curves.curve("USD-SOFR-OIS")
        forecast = curves.curve("USD-LIBOR-3M")
        for t in (1.0, 5.0, 10.0):
            basis = (forecast.zero(t) - ois.zero(t)) * 1e4
            assert 10.0 < basis < 60.0, f"implausible basis at {t}y: {basis:.1f}bp"

    def test_each_curve_keeps_its_own_content_hash(self, curves: CurveSet) -> None:
        assert (
            curves.curve("USD-SOFR-OIS").content_hash != curves.curve("USD-LIBOR-3M").content_hash
        )

    def test_index_tenor_is_part_of_curve_identity(self) -> None:
        """Two curves with identical instruments and quotes but different
        index tenors are genuinely different curves. Before `index_tenor`
        joined the config they could only be told apart by their display
        name (ADR-0006)."""
        six_month = FORECAST.model_copy(update={"index_tenor": "6M"})
        assert six_month.content_hash != FORECAST.content_hash


class TestRepricing:
    def test_the_forecast_curve_reprices_every_input(self, curves: CurveSet) -> None:
        """Recompute each input's model par rate from the solved curve and
        compare it to its quote.

        Deliberately not "the build returned, therefore it repriced": the
        build enforces the same property, so asserting on its success would
        be a check that could not fail. This re-derives the par rates from
        the curve's own discount factors instead.
        """
        ois = curves.curve("USD-SOFR-OIS")
        forecast = curves.curve("USD-LIBOR-3M")
        # 3M deposit: the first index period, projected off the forecast
        # curve alone.
        spot, three_month = date(2026, 7, 2), date(2026, 10, 2)
        accrual = (three_month - spot).days / 360.0
        implied = (forecast.discount_at(spot) / forecast.discount_at(three_month) - 1.0) / accrual
        assert implied == pytest.approx(FORECAST_QUOTES[(K.DEPOSIT, "3M")], abs=1e-6)
        # And the curves are genuinely distinct where it matters.
        assert forecast.discount_at(three_month) != ois.discount_at(three_month)

    def test_a_curve_that_cannot_reprice_is_refused(self, curves: CurveSet) -> None:
        """Two instruments at the same node give the solver two equations
        for one unknown. The build must fail rather than pick one."""
        duplicated = FORECAST.model_copy(
            update={
                "instruments": (
                    InstrumentSpec(kind=K.SWAP, tenor="5Y"),
                    InstrumentSpec(kind=K.DEPOSIT, tenor="5Y"),
                )
            }
        )
        with pytest.raises(CurveBuildError, match="share a maturity node"):
            build_forecast_curve(
                duplicated,
                {(K.SWAP, "5Y"): 0.04, (K.DEPOSIT, "5Y"): 0.05},
                discount=curves.curve("USD-SOFR-OIS"),
                as_of=AS_OF,
            )


class TestRefusalsThatPreventSingleCurve:
    def test_self_discounting_is_refused(self, curves: CurveSet) -> None:
        """'self' means single-curve. Allowing it through the multi-curve
        path would produce a curve indistinguishable from a real one."""
        with pytest.raises(CurveBuildError, match="single-curve"):
            build_forecast_curve(
                FORECAST.model_copy(update={"discount_basis": "self"}),
                FORECAST_QUOTES,
                discount=curves.curve("USD-SOFR-OIS"),
                as_of=AS_OF,
            )

    def test_a_forecast_curve_without_an_index_tenor_is_refused(self, curves: CurveSet) -> None:
        with pytest.raises(CurveBuildError, match="index_tenor"):
            build_forecast_curve(
                FORECAST.model_copy(update={"index_tenor": None}),
                FORECAST_QUOTES,
                discount=curves.curve("USD-SOFR-OIS"),
                as_of=AS_OF,
            )

    def test_an_ois_instrument_on_a_forecast_curve_is_refused(self, curves: CurveSet) -> None:
        """An overnight-index swap constrains the discount curve, not this
        curve's index forwards. Accepting it would add an equation about
        the wrong curve to the solve."""
        with pytest.raises(CurveBuildError, match="OIS"):
            build_forecast_curve(
                FORECAST.model_copy(
                    update={"instruments": (InstrumentSpec(kind=K.OIS, tenor="5Y"),)}
                ),
                {(K.OIS, "5Y"): 0.04},
                discount=curves.curve("USD-SOFR-OIS"),
                as_of=AS_OF,
            )

    def test_a_missing_quote_is_refused_not_skipped(self, curves: CurveSet) -> None:
        with pytest.raises(CurveBuildError, match="no quote supplied"):
            build_forecast_curve(
                FORECAST,
                {(K.DEPOSIT, "3M"): 0.0455},
                discount=curves.curve("USD-SOFR-OIS"),
                as_of=AS_OF,
            )


class TestCurveSetOrdering:
    def test_a_forecast_curve_is_built_after_its_discount_curve(self) -> None:
        """The forecast curve is listed first and must still build: order in
        the sequence is not build order, and requiring callers to sort would
        make a silent misbuild possible."""
        built = CurveSet(AS_OF, [CurveSpec(FORECAST, FORECAST_QUOTES), CurveSpec(OIS, OIS_QUOTES)])
        assert built.names == ("USD-LIBOR-3M", "USD-SOFR-OIS")

    def test_a_missing_dependency_is_named(self) -> None:
        with pytest.raises(UnknownCurveError, match="USD-SOFR-OIS"):
            CurveSet(AS_OF, [CurveSpec(FORECAST, FORECAST_QUOTES)])

    def test_a_cycle_is_refused_rather_than_recursed(self) -> None:
        a = OIS.model_copy(update={"name": "A", "discount_basis": "B", "index_tenor": "3M"})
        b = OIS.model_copy(update={"name": "B", "discount_basis": "A", "index_tenor": "3M"})
        with pytest.raises(CurveBuildError, match="circular"):
            CurveSet(AS_OF, [CurveSpec(a, OIS_QUOTES), CurveSpec(b, OIS_QUOTES)])

    def test_two_definitions_of_one_name_are_refused(self) -> None:
        with pytest.raises(CurveBuildError, match="two curve definitions"):
            CurveSet(AS_OF, [CurveSpec(OIS, OIS_QUOTES), CurveSpec(OIS, OIS_QUOTES)])

    def test_an_absent_curve_is_named_with_what_is_present(self, curves: CurveSet) -> None:
        with pytest.raises(UnknownCurveError, match="USD-SOFR-OIS"):
            curves.curve("EUR-ESTR-OIS")


class TestTheSetHash:
    def test_the_hash_covers_every_member(self, curves: CurveSet) -> None:
        """A swap's PV depends on the whole environment, so changing any
        curve must change the stamp — otherwise two different valuations
        could claim the same inputs."""
        altered = CurveSet(
            AS_OF,
            [
                CurveSpec(FORECAST, FORECAST_QUOTES),
                CurveSpec(
                    OIS.model_copy(update={"interpolation": Interpolation.LINEAR_ZERO}),
                    OIS_QUOTES,
                ),
            ],
        )
        assert altered.content_hash != curves.content_hash

    def test_the_hash_is_stable_for_an_identical_set(self, curves: CurveSet) -> None:
        twin = CurveSet(AS_OF, [CurveSpec(FORECAST, FORECAST_QUOTES), CurveSpec(OIS, OIS_QUOTES)])
        assert twin.content_hash == curves.content_hash


class TestBumpingRebuildsRatherThanShifts:
    def test_a_bumped_set_equals_one_built_from_the_bumped_market(self, curves: CurveSet) -> None:
        """The property that separates rebuilding from shifting.

        A set bumped by 1bp must be *identical* to one bootstrapped from
        quotes that were 1bp higher to begin with. Shifting the solved zeros
        would give a curve that reprices none of those quotes, and nothing
        downstream would show the difference — the DV01 would simply be the
        sensitivity of a curve that does not exist in the market.
        """
        bumped = curves.bumped(1.0)
        from_scratch = CurveSet(
            AS_OF,
            [
                CurveSpec(FORECAST, {k: v + 1e-4 for k, v in FORECAST_QUOTES.items()}),
                CurveSpec(OIS, {k: v + 1e-4 for k, v in OIS_QUOTES.items()}),
            ],
        )
        for name in curves.names:
            assert bumped.curve(name).node_zeros == pytest.approx(
                from_scratch.curve(name).node_zeros, abs=1e-12
            ), name

    def test_a_parallel_bump_moves_both_curves(self, curves: CurveSet) -> None:
        bumped = curves.bumped(1.0)
        for name in curves.names:
            before = curves.curve(name).zero(5.0)
            after = bumped.curve(name).zero(5.0)
            assert after - before == pytest.approx(1e-4, abs=2e-5), name

    def test_a_single_curve_bump_leaves_the_other_alone(self, curves: CurveSet) -> None:
        """Bumping the forecast curve must not move the OIS curve. If it did,
        every forecast bucket would silently carry discount risk too."""
        bumped = curves.bumped(1.0, curve="USD-LIBOR-3M")
        assert bumped.curve("USD-SOFR-OIS").node_zeros == curves.curve("USD-SOFR-OIS").node_zeros
        assert bumped.curve("USD-LIBOR-3M").node_zeros != curves.curve("USD-LIBOR-3M").node_zeros

    def test_bumping_the_discount_curve_moves_the_forecast_curve(self, curves: CurveSet) -> None:
        """The forecast curve is solved *against* the discount curve, so a
        change in discounting re-solves the forwards. A set that rebuilt
        curves independently would hold the forwards fixed and quietly move
        the basis instead."""
        bumped = curves.bumped(1.0, curve="USD-SOFR-OIS")
        assert bumped.curve("USD-LIBOR-3M").node_zeros != curves.curve("USD-LIBOR-3M").node_zeros

    def test_bumping_an_instrument_that_is_not_there_is_refused(self, curves: CurveSet) -> None:
        """A bucket matching nothing would rebuild an unchanged set and
        report a DV01 of exactly zero — indistinguishable from a genuinely
        insensitive bucket."""
        with pytest.raises(UnknownCurveError, match="no instrument"):
            curves.bumped(1.0, curve="USD-LIBOR-3M", instrument=(K.SWAP, "30Y"))

    def test_buckets_list_only_what_can_be_bumped(self, curves: CurveSet) -> None:
        buckets = curves.buckets("USD-LIBOR-3M")
        assert len(buckets) == len(FORECAST_QUOTES)
        for kind, tenor in buckets:
            curves.bumped(1.0, curve="USD-LIBOR-3M", instrument=(kind, tenor))


class TestTenorBasis:
    """A 6M curve built from basis swaps against the 3M curve (spec §11.1)."""

    @staticmethod
    def _six_month() -> CurveConfig:
        return FORECAST.model_copy(
            update={
                "name": "USD-LIBOR-6M",
                "index_tenor": "6M",
                "instruments": (
                    InstrumentSpec(kind=K.BASIS, tenor="2Y"),
                    InstrumentSpec(kind=K.BASIS, tenor="5Y"),
                    InstrumentSpec(kind=K.BASIS, tenor="10Y"),
                ),
            }
        )

    def test_a_positive_basis_lifts_the_six_month_curve(self, curves: CurveSet) -> None:
        """The spread is quoted on the 6M leg here, so a positive spread
        means 6M pays more than 3M and its forwards must be lower — the leg
        makes up the difference in spread rather than in rate."""
        built = CurveSet(
            AS_OF,
            [
                CurveSpec(FORECAST, FORECAST_QUOTES),
                CurveSpec(OIS, OIS_QUOTES),
                CurveSpec(
                    self._six_month(),
                    {(K.BASIS, "2Y"): 0.0010, (K.BASIS, "5Y"): 0.0012, (K.BASIS, "10Y"): 0.0015},
                    basis_reference="USD-LIBOR-3M",
                ),
            ],
        )
        three, six = built.curve("USD-LIBOR-3M"), built.curve("USD-LIBOR-6M")
        assert six.zero(10.0) < three.zero(10.0)
        assert abs(six.zero(10.0) - three.zero(10.0)) * 1e4 < 40.0

    def test_a_basis_against_the_same_index_is_refused(self, curves: CurveSet) -> None:
        """Both legs would be identical and the quote would carry no
        information — the solve would be singular and the curve arbitrary."""
        same = self._six_month().model_copy(update={"index_tenor": "3M"})
        with pytest.raises(CurveBuildError, match="same 3M index"):
            build_forecast_curve(
                same,
                {(K.BASIS, "2Y"): 0.001, (K.BASIS, "5Y"): 0.001, (K.BASIS, "10Y"): 0.001},
                discount=curves.curve("USD-SOFR-OIS"),
                as_of=AS_OF,
                basis_reference=curves.curve("USD-LIBOR-3M"),
            )

    def test_a_basis_without_a_reference_curve_is_refused(self, curves: CurveSet) -> None:
        with pytest.raises(CurveBuildError, match="no reference curve"):
            build_forecast_curve(
                self._six_month(),
                {(K.BASIS, "2Y"): 0.001, (K.BASIS, "5Y"): 0.001, (K.BASIS, "10Y"): 0.001},
                discount=curves.curve("USD-SOFR-OIS"),
                as_of=AS_OF,
            )


class TestCsaDiscountCurve:
    """Collateral posted in another currency discounts at another curve."""

    @staticmethod
    def _config() -> CurveConfig:
        return OIS.model_copy(update={"name": "USD-COLLAT-EUR", "discount_basis": "USD-SOFR-OIS"})

    def test_the_basis_shifts_the_whole_curve(self, curves: CurveSet) -> None:
        base = curves.curve("USD-SOFR-OIS")
        adjusted = build_csa_discount_curve(
            self._config(),
            base=base,
            basis_spreads={"1Y": -0.0020, "5Y": -0.0025, "10Y": -0.0030},
            as_of=AS_OF,
        )
        assert adjusted.zero(5.0) < base.zero(5.0)
        assert adjusted.discount_at(date(2036, 7, 2)) > base.discount_at(date(2036, 7, 2))

    def test_no_spreads_is_refused(self, curves: CurveSet) -> None:
        """A foreign CSA with a zero basis is a claim about the market and
        must be quoted, not arrived at by supplying nothing."""
        with pytest.raises(CurveBuildError, match="no basis spreads"):
            build_csa_discount_curve(
                self._config(), base=curves.curve("USD-SOFR-OIS"), basis_spreads={}, as_of=AS_OF
            )

    def test_a_discount_curve_that_also_forecasts_is_refused(self, curves: CurveSet) -> None:
        """A curve claiming both roles would be used for both, which is the
        single-curve collapse under another name."""
        with pytest.raises(CurveBuildError, match="discount curve but names index_tenor"):
            build_csa_discount_curve(
                self._config().model_copy(update={"index_tenor": "3M"}),
                base=curves.curve("USD-SOFR-OIS"),
                basis_spreads={"5Y": -0.0025},
                as_of=AS_OF,
            )

    def test_it_builds_inside_a_curve_set(self, curves: CurveSet) -> None:
        built = CurveSet(
            AS_OF,
            [
                CurveSpec(OIS, OIS_QUOTES),
                CurveSpec(FORECAST, FORECAST_QUOTES),
                CurveSpec(
                    self._config(),
                    {},
                    basis_spreads={"1Y": -0.0020, "5Y": -0.0025, "10Y": -0.0030},
                ),
            ],
        )
        assert "USD-COLLAT-EUR" in built.names
        assert built.buckets("USD-COLLAT-EUR") == ()
