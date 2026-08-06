"""TVAL Prong 2 — relative value against an issuer curve (spec §15.1).

Prong 2 was recorded as blocked on rating and seniority. Both are genuinely
absent, and the conclusion drawn from that was too wide: the other half of
§15.1 is an issuer curve, which needs issuer, maturity and price. These tests
cover the half that can be built and pin the half that cannot — a comparable
set must *say* which dimensions it could not match on, because one that
silently dropped two of three would compare a senior secured note with a
subordinated one and look confident doing it.
"""

from __future__ import annotations

from datetime import date

import pytest

from treble.analytics.tval.relative import (
    AVAILABLE_DIMENSIONS,
    FAIR_BAND_BP,
    MIN_CURVE_BONDS,
    REQUIRED_DIMENSIONS,
    ComparableSet,
    InsufficientBondsError,
    IssuerBond,
    fit_issuer_curve,
    relative_value,
)

AS_OF = date(2026, 6, 1)
_fit = fit_issuer_curve.__wrapped__
_value = relative_value.__wrapped__


def _bond(name: str, years: float, yield_: float, **kwargs: object) -> IssuerBond:
    return IssuerBond(
        identifier=name,
        maturity=date.fromordinal(AS_OF.toordinal() + round(years * 365.25)),
        yield_=yield_,
        currency=str(kwargs.get("currency", "USD")),
        issuer_category=kwargs.get("issuer_category", "CORP"),  # type: ignore[arg-type]
    )


class TestTheCurveFits:
    def test_it_recovers_a_line_it_was_given(self) -> None:
        """The only check with a true answer: yields laid exactly on a line
        must return that line's intercept and slope.

        The yields are computed from each bond's *realised* year fraction
        rather than the requested one — a maturity is a date, so asking for
        2.0 years lands on 1.9986. Laying the line on the requested years
        would leave a residual that is the helper's rounding, and the test
        would then be measuring its own fixture."""
        bonds = [_bond(f"B{i}", 1.0 + i, 0.0) for i in range(5)]
        bonds = [
            IssuerBond(
                identifier=b.identifier,
                maturity=b.maturity,
                yield_=0.03 + 0.002 * b.years_to(AS_OF),
                currency=b.currency,
                issuer_category=b.issuer_category,
            )
            for b in bonds
        ]
        curve = _fit("ACME", bonds, as_of=AS_OF)
        assert curve.intercept == pytest.approx(0.03, abs=1e-6)
        assert curve.slope_bp_per_year == pytest.approx(20.0, abs=0.05)
        assert curve.residual_rms_bp == pytest.approx(0.0, abs=1e-6)

    def test_a_bond_off_the_line_is_the_only_one_called(self) -> None:
        bonds = [_bond(f"B{i}", 1.0 + i, 0.03 + 0.002 * (1.0 + i)) for i in range(5)]
        cheap = bonds[2]
        bonds[2] = IssuerBond(
            identifier=cheap.identifier,
            maturity=cheap.maturity,
            yield_=cheap.yield_ + 0.01,
            currency=cheap.currency,
            issuer_category=cheap.issuer_category,
        )
        curve = _fit("ACME", bonds, as_of=AS_OF)
        verdicts = {b.identifier: _value(b, curve).verdict for b in bonds}
        assert verdicts["B2"] == "cheap"
        assert set(verdicts.values()) == {"fair", "cheap"}

    def test_cheap_means_yields_more_than_the_curve(self) -> None:
        """The sign convention, asserted because it is exactly backwards
        half the time: a higher yield is a lower price, which is cheap."""
        bonds = [_bond(f"B{i}", 1.0 + i, 0.04) for i in range(4)]
        curve = _fit("ACME", bonds, as_of=AS_OF)
        dearer = _bond("RICH", 2.0, 0.04 - 0.01)
        cheaper = _bond("CHEAP", 2.0, 0.04 + 0.01)
        assert _value(dearer, curve).verdict == "rich"
        assert _value(cheaper, curve).verdict == "cheap"
        assert _value(cheaper, curve).residual_bp > 0


class TestItRefusesRatherThanFittingSomethingMeaningless:
    def test_two_bonds_are_refused(self) -> None:
        """Two points fit a line exactly and leave no residual, so every
        bond prices on its own curve and nothing is ever rich or cheap — a
        relative value engine that cannot find anything."""
        with pytest.raises(InsufficientBondsError, match="fewest a curve"):
            _fit("ACME", [_bond("A", 1.0, 0.03), _bond("B", 2.0, 0.04)], as_of=AS_OF)

    def test_mixed_currencies_are_refused(self) -> None:
        """A yield difference across currencies is the rate differential,
        not the issuer's credit."""
        bonds = [
            _bond("A", 1.0, 0.03),
            _bond("B", 2.0, 0.04),
            _bond("C", 3.0, 0.05, currency="EUR"),
        ]
        with pytest.raises(ValueError, match="cannot share a curve"):
            _fit("ACME", bonds, as_of=AS_OF)

    def test_bonds_all_maturing_on_one_day_are_refused(self) -> None:
        """There is a level but no term structure.

        This found a real defect. The guard was `variance == 0`, and three
        bonds sharing a maturity produce a variance of ~1e-32 rather than
        zero, so the exact comparison passed and the fit divided one denormal
        by another. It returned a slope of 0.0 on this input by luck."""
        bonds = [_bond(name, 2.0, 0.03 + i * 0.001) for i, name in enumerate("ABC")]
        with pytest.raises(ValueError, match="no slope"):
            _fit("ACME", bonds, as_of=AS_OF)

    def test_a_matured_bond_is_refused(self) -> None:
        bonds = [_bond("A", 1.0, 0.03), _bond("B", 2.0, 0.04), _bond("C", -0.5, 0.02)]
        with pytest.raises(ValueError, match="matures on or before"):
            _fit("ACME", bonds, as_of=AS_OF)

    def test_the_minimum_is_three(self) -> None:
        assert MIN_CURVE_BONDS == 3


class TestTheNoiseTravelsWithTheCall:
    def test_a_residual_inside_the_curves_own_scatter_is_not_significant(self) -> None:
        """A rich/cheap call smaller than the fit's own RMS is the fit's
        error being reported as value. The verdict may still read rich or
        cheap; `is_significant` is what says whether to believe it."""
        # A deliberately scattered curve: large RMS, so a modest residual
        # must not clear the bar.
        bonds = [
            _bond("A", 1.0, 0.030),
            _bond("B", 2.0, 0.055),
            _bond("C", 3.0, 0.035),
            _bond("D", 4.0, 0.060),
        ]
        curve = _fit("NOISY", bonds, as_of=AS_OF)
        assert curve.residual_rms_bp > FAIR_BAND_BP
        modest = _bond("X", 2.5, curve.yield_at(2.5) + curve.residual_rms_bp / 2e4)
        assessed = _value(modest, curve)
        assert assessed.is_significant is False
        assert assessed.curve_residual_rms_bp == pytest.approx(curve.residual_rms_bp)

    def test_a_residual_beyond_the_scatter_is_significant(self) -> None:
        bonds = [_bond(f"B{i}", 1.0 + i, 0.03 + 0.002 * (1.0 + i)) for i in range(4)]
        curve = _fit("TIGHT", bonds, as_of=AS_OF)
        wide = _bond("X", 2.5, curve.yield_at(2.5) + 0.005)
        assert _value(wide, curve).is_significant is True

    def test_a_tight_curve_still_needs_the_fair_band(self) -> None:
        """With a near-perfect fit the RMS approaches zero, and every bond a
        basis point off would be 'significant'. `FAIR_BAND_BP` floors it:
        month-end fund marks differ from traded levels for reasons that are
        not value."""
        bonds = [_bond(f"B{i}", 1.0 + i, 0.03 + 0.002 * (1.0 + i)) for i in range(4)]
        curve = _fit("TIGHT", bonds, as_of=AS_OF)
        assert curve.residual_rms_bp < 1.0
        barely = _bond("X", 2.5, curve.yield_at(2.5) + 0.0005)  # 5bp
        assert _value(barely, curve).is_significant is False


class TestTheComparableSetSaysWhatItCouldNotMatchOn:
    def test_missing_dimensions_are_reported_not_dropped(self) -> None:
        """§15.1 asks for sector, rating and seniority. The store holds none
        of them, so every comparable set here is incomplete and says so. A
        set that reported only its members would let a reader believe a
        subordinated note had been excluded when nothing had looked."""
        target = _bond("T", 3.0, 0.04)
        universe = [target, _bond("A", 3.5, 0.041), _bond("B", 9.0, 0.05)]
        result = ComparableSet.around(target, universe, as_of=AS_OF)
        assert result.is_complete is False
        assert set(result.missing_dimensions) == set(REQUIRED_DIMENSIONS)
        assert result.dimensions == AVAILABLE_DIMENSIONS

    def test_it_matches_on_maturity_currency_and_category(self) -> None:
        target = _bond("T", 3.0, 0.04)
        universe = [
            target,
            _bond("NEAR", 3.5, 0.041),
            _bond("FAR", 9.0, 0.05),
            _bond("OTHER_CCY", 3.2, 0.04, currency="EUR"),
            _bond("OTHER_CAT", 3.1, 0.04, issuer_category="UST"),
        ]
        members = ComparableSet.around(target, universe, as_of=AS_OF).members
        assert members == ("NEAR",)

    def test_the_target_is_not_its_own_comparable(self) -> None:
        target = _bond("T", 3.0, 0.04)
        assert "T" not in ComparableSet.around(target, [target], as_of=AS_OF).members

    def test_required_and_available_dimensions_do_not_overlap(self) -> None:
        """Guards the honesty of `is_complete`: if a dimension appeared in
        both lists the set would claim to match on something §15.1 asked for
        and still report it missing, or the reverse."""
        assert not set(REQUIRED_DIMENSIONS) & set(AVAILABLE_DIMENSIONS)
