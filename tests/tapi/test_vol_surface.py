"""VCUB's surface build (spec §11.3).

The second module found at 0% coverage while the repository floor passed.
The first, `tapi/issuer_curves.py`, was fitting issuer curves over
securitisations, and nothing noticed because nothing looked. These are the
tests that look.

What is checked here is the part `analytics/vol/surface.py` cannot check for
itself: that each day's prints are valued against *that day's* curves, that a
day with no curve pair is skipped rather than substituted, and that the
counts a caller uses to judge the surface are the real ones.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.swap_market import DISCOUNT_CURVE, FORECAST_CURVE
from treble.tapi.vol_surface import VolSurfaceUnavailableError, build_vol_surface

KNOWN = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
DAY_ONE = date(2026, 7, 13)
DAY_TWO = date(2026, 7, 14)

RATES = {
    "1Y": 0.0295,
    "2Y": 0.0301,
    "3Y": 0.0303,
    "5Y": 0.0305,
    "7Y": 0.0311,
    "10Y": 0.0322,
    "20Y": 0.0338,
    "30Y": 0.0330,
}


def _provenance() -> Provenance:
    return Provenance(
        source_system="dtcc-sdr",
        source_uri="https://example.invalid/CFTC_CUMULATIVE_RATES.zip",
        retrieved_at=KNOWN,
        method=ExtractionMethod.BULK_FILE,
        extractor_version="1",
        payload_hash="0" * 64,
    )


def _fact(subject: str, field: str, value: object, day: date, prov: str) -> Fact:
    return Fact(
        subject=subject,
        field=field,
        value=value,
        effective_from=day,
        effective_to=day,
        knowledge_from=KNOWN,
        provenance_id=prov,
    )


def _write_curves(store: DuckStore, prov: Provenance, days: list[date]) -> None:
    facts = [
        _fact(f"swap:{curve}:{tenor}", "PAR_RATE", rate, day, prov.id)
        for day in days
        for curve in (DISCOUNT_CURVE, FORECAST_CURVE)
        for tenor, rate in RATES.items()
    ]
    store.write_facts(facts)


def _write_print(
    store: DuckStore,
    prov: Provenance,
    *,
    key: str,
    day: date,
    expiry: date,
    maturity: date,
    strike: float,
    premium: float,
) -> None:
    store.write_facts(
        [
            _fact(f"swaption:EUR:{key}", field, value, day, prov.id)
            for field, value in (
                ("EXPIRY_DATE", expiry),
                ("UNDERLIER_MATURITY", maturity),
                ("STRIKE", strike),
                ("PREMIUM_FRACTION", premium),
                ("PAYER", True),
                ("NOTIONAL_CAPPED", False),
            )
        ]
    )


@pytest.fixture
def store(tmp_path: Path) -> DuckStore:
    return DuckStore(tmp_path / "t.db")


class TestWhenItCannotBuild:
    def test_no_prints_at_all_says_where_they_come_from(self, store: DuckStore) -> None:
        """An empty store and a broken parser render the same on a screen.

        The message names the adapter that supplies the prints, because "no
        surface" is a question about ingest and the person reading it should
        not have to go looking for which source that is.
        """
        with pytest.raises(VolSurfaceUnavailableError, match="DTCC adapter"):
            build_vol_surface(store, as_of=datetime.now(UTC))

    def test_prints_with_no_curve_for_their_day_are_refused_not_substituted(
        self, store: DuckStore
    ) -> None:
        """The measured mistake this module was written around.

        Valuing a print from the 13th against the 31st's curve carries an
        eighteen-day-stale forward, which misplaces its moneyness and
        inflates its implied vol most where vega is smallest — node
        dispersion went from under 20% to 84-105%. So a day with no curve
        pair is skipped and counted, never valued off another day's.
        """
        prov = _provenance()
        store.write_provenance([prov])
        _write_curves(store, prov, [DAY_TWO])
        _write_print(
            store,
            prov,
            key="A",
            day=DAY_ONE,
            expiry=date(2027, 7, 13),
            maturity=date(2037, 7, 13),
            strike=0.0305,
            premium=0.012,
        )
        with pytest.raises(VolSurfaceUnavailableError, match="no curve pair"):
            build_vol_surface(store, as_of=datetime.now(UTC))


class TestTheCountsAreReal:
    def test_a_built_surface_reports_what_it_rested_on(self, store: DuckStore) -> None:
        """`solve_rate` is the number that says how much of the tape this
        surface actually stands on, so it must count prints read rather than
        prints that happened to work."""
        prov = _provenance()
        store.write_provenance([prov])
        _write_curves(store, prov, [DAY_ONE])
        # Struck around the 1Y10Y forward this curve implies (0.032802) with
        # premiums above intrinsic. The first draft used 0.0305/0.012, which
        # is 23bp in the money against an intrinsic of 0.0189 — the solver
        # refused it, correctly, and the refusal is why these numbers are
        # measured off the curve rather than picked to look plausible.
        for n, (strike, premium) in enumerate([(0.0325, 0.030), (0.0330, 0.028), (0.0320, 0.032)]):
            _write_print(
                store,
                prov,
                key=f"P{n}",
                day=DAY_ONE,
                expiry=date(2027, 7, 13),
                maturity=date(2037, 7, 13),
                strike=strike,
                premium=premium,
            )
        built = build_vol_surface(store, as_of=datetime.now(UTC))
        assert built.prints_read == 3
        assert built.days_used == 1
        assert built.days_without_curves == 0
        assert 0.0 < built.solve_rate <= 1.0
        assert built.prints_solved == pytest.approx(built.solve_rate * built.prints_read)

    def test_solve_rate_is_zero_rather_than_dividing_by_zero(self) -> None:
        """A guard that only ever runs on a populated build is a guard
        nobody has seen work."""
        from treble.tapi.vol_surface import SurfaceBuild

        empty = SurfaceBuild(
            surface=None,  # type: ignore[arg-type]
            prints_read=0,
            prints_solved=0,
            days_used=0,
            days_without_curves=0,
        )
        assert empty.solve_rate == 0.0
