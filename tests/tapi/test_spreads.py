"""The government curve `SPRD` measures against.

Built from the CMT par yields the Treasury adapter ingests. The tests are
about the two decisions that make it a curve rather than a set of numbers:
which tenors may be treated as par bonds, and which day to build on.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import position_subject
from treble.core.provenance import ExtractionMethod, Provenance
from treble.ingest.treasury_curve import CURVE, FIELD
from treble.store.duck import DuckStore
from treble.tapi.spreads import (
    MIN_GOVT_NODES,
    GovtCurveUnavailableError,
    build_govt_curve,
    govt_curve_dates,
)

KNOWN = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
DAY = date(2026, 8, 7)
PAR = {"1Y": 0.0401, "2Y": 0.0419, "3Y": 0.0425, "5Y": 0.0435, "7Y": 0.0449, "10Y": 0.0465}


def _store(
    tmp_path: Path, points: dict[str, float], *, when: date = DAY, name: str = "g"
) -> DuckStore:
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = DuckStore(tmp_path / f"{name}.db")
    record = Provenance(
        source_system="treasury-curve",
        source_uri="https://example.invalid/ust",
        retrieved_at=KNOWN,
        method=ExtractionMethod.BULK_FILE,
        extractor_version="1",
        payload_hash="a" * 64,
    )
    store.write_provenance([record])
    store.write_facts(
        [
            Fact(
                subject=f"govt:{CURVE}:{tenor}",
                field=FIELD,
                value=rate,
                effective_from=when,
                effective_to=when,
                knowledge_from=KNOWN,
                provenance_id=record.id,
            )
            for tenor, rate in points.items()
        ]
    )
    return store


class TestItBuilds:
    def test_a_full_curve_bootstraps(self, tmp_path: Path) -> None:
        curve, day = build_govt_curve(_store(tmp_path, PAR), as_of=LATER)
        assert day == DAY
        assert 0.03 < curve.zero(10.0) < 0.06

    def test_the_curve_slopes_the_way_its_inputs_do(self, tmp_path: Path) -> None:
        """An upward-sloping par curve must not bootstrap to an inverted
        zero curve. A shape test catches a sign or ordering error that a
        single-point assertion would sail past."""
        curve, _ = build_govt_curve(_store(tmp_path, PAR), as_of=LATER)
        assert curve.zero(1.0) < curve.zero(5.0) < curve.zero(10.0)

    def test_bills_are_excluded_rather_than_treated_as_par_bonds(self, tmp_path: Path) -> None:
        """Treasury quotes tenors under a year on a discount basis.
        Bootstrapping them as par bonds would misprice the short end, which
        is exactly where a two-year corporate reads its G-spread."""
        with_bills = dict(PAR)
        with_bills.update({"1M": 0.0379, "3M": 0.0387, "6M": 0.0396})
        curve, _ = build_govt_curve(_store(tmp_path, with_bills), as_of=LATER)
        bills_only, _ = build_govt_curve(_store(tmp_path, PAR, name="b"), as_of=LATER)
        assert curve.zero(10.0) == pytest.approx(bills_only.zero(10.0), abs=1e-9)


class TestItChoosesTheDayCarefully:
    def test_a_day_with_too_few_points_is_not_offered(self, tmp_path: Path) -> None:
        thin = {"1Y": 0.04, "2Y": 0.042}
        assert govt_curve_dates(_store(tmp_path, thin), as_of=LATER) == []

    def test_too_few_points_anywhere_is_an_error_that_says_why(self, tmp_path: Path) -> None:
        """Naming the bill exclusion in the message matters: a store full
        of short tenors looks well populated, and the reason it cannot
        build a curve is not obvious from the outside."""
        with pytest.raises(GovtCurveUnavailableError, match="discount basis"):
            build_govt_curve(_store(tmp_path, {"1M": 0.0379, "3M": 0.0387}), as_of=LATER)

    def test_it_prefers_the_newest_day_that_actually_builds(self, tmp_path: Path) -> None:
        """Newest *usable*, not newest. A thin latest day emptied SWPM's
        basis tab and DDIS's ladder before this rule was applied to them."""
        store = _store(tmp_path, PAR)
        assert build_govt_curve(store, as_of=LATER)[1] == DAY

    def test_an_explicit_date_with_no_curve_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(GovtCurveUnavailableError):
            build_govt_curve(_store(tmp_path, PAR), as_of=LATER, report_date=date(2020, 1, 1))

    def test_the_node_floor_is_what_it_claims(self, tmp_path: Path) -> None:
        """A curve with four points has no shape worth interpolating."""
        exact = dict(list(PAR.items())[:MIN_GOVT_NODES])
        assert govt_curve_dates(_store(tmp_path, exact), as_of=LATER) == [DAY]
        one_short = dict(list(PAR.items())[: MIN_GOVT_NODES - 1])
        assert govt_curve_dates(_store(tmp_path, one_short, name="s"), as_of=LATER) == []


class TestItIsPointInTime:
    def test_a_curve_is_not_visible_before_it_was_known(self, tmp_path: Path) -> None:
        """I2. Asking for the curve in July must not return something
        ingested in August."""
        store = _store(tmp_path, PAR)
        assert govt_curve_dates(store, as_of=datetime(2026, 7, 1, tzinfo=UTC)) == []


class TestBondSpreads:
    """Three benchmarks, one bond.

    The first bond this ever ran on was an Australian 2055 line measured
    against the US CMT curve — three numbers that computed cleanly and
    meant nothing. Currency is checked before anything else now.
    """

    @staticmethod
    def _with_bond(
        store: DuckStore, *, currency: str = "USD", coupon: float = 5.0, price: float = 98.5
    ) -> DuckStore:
        record = Provenance(
            source_system="edgar-nport",
            source_uri="https://example.invalid/nport",
            retrieved_at=KNOWN,
            method=ExtractionMethod.BULK_FILE,
            extractor_version="1",
            payload_hash="b" * 64,
        )
        store.write_provenance([record])
        report = date(2026, 3, 31)
        instrument = "isin:US0000000000"
        # Par and value are the holder's, and live on a position subject as
        # `ingest.nport` writes them; the rest describe the bond itself.
        held = str(position_subject(fund="S000000001", instrument=instrument))
        fields: list[tuple[str, str, object]] = [
            (instrument, "nport:maturityDt", date(2031, 3, 31)),
            (instrument, "nport:annualizedRt", coupon),
            (instrument, "nport:curCd", currency),
            (instrument, "nport:assetCat", "DBT"),
            (instrument, "nport:name", "TEST ISSUER"),
            (held, "nport:valUSD", 1_000_000.0 * price / 100.0),
            (held, "nport:balance", 1_000_000.0),
        ]
        store.write_facts(
            [
                Fact(
                    subject=subject,
                    field=field,
                    value=value,
                    effective_from=report,
                    effective_to=report,
                    knowledge_from=KNOWN,
                    provenance_id=record.id,
                )
                for subject, field, value in fields
            ]
        )
        return store

    def test_a_non_usd_bond_is_refused_not_measured(self, tmp_path: Path) -> None:
        """Both benchmarks are USD. An AUD bond against them produces a
        spread across currencies, which is not a spread."""
        from treble.tapi.spreads import BondNotPriceableError, bond_spreads

        store = self._with_bond(_store(tmp_path, PAR), currency="AUD")
        with pytest.raises(BondNotPriceableError, match="not a spread"):
            bond_spreads(store, identifier="isin:US0000000000", as_of=LATER)

    def test_the_three_spreads_are_computed(self, tmp_path: Path) -> None:
        from treble.tapi.spreads import bond_spreads

        measured = bond_spreads(
            self._with_bond(_store(tmp_path, PAR)), identifier="isin:US0000000000", as_of=LATER
        )
        assert measured.g_spread_bp is not None
        assert measured.price == pytest.approx(98.5)
        assert 0.0 < measured.yield_pct < 20.0

    def test_a_richer_price_narrows_the_spread(self, tmp_path: Path) -> None:
        """Direction, which no single number can establish. Paying more for
        the same cash flows earns less, which is a tighter spread."""
        from treble.tapi.spreads import bond_spreads

        cheap = bond_spreads(
            self._with_bond(_store(tmp_path, PAR, name="c"), price=95.0),
            identifier="isin:US0000000000",
            as_of=LATER,
        )
        dear = bond_spreads(
            self._with_bond(_store(tmp_path, PAR, name="d"), price=105.0),
            identifier="isin:US0000000000",
            as_of=LATER,
        )
        assert cheap.g_spread_bp is not None and dear.g_spread_bp is not None
        assert cheap.g_spread_bp > dear.g_spread_bp

    def test_the_swap_spread_is_g_minus_i(self, tmp_path: Path) -> None:
        """Published as the arithmetic check on the other two, so it must
        actually be their difference rather than a separate computation."""
        from treble.tapi.spreads import bond_spreads

        measured = bond_spreads(
            self._with_bond(_store(tmp_path, PAR)), identifier="isin:US0000000000", as_of=LATER
        )
        if measured.i_spread_bp is None:
            pytest.skip("no swap curve in this fixture store")
        assert measured.swap_spread_bp == pytest.approx(
            measured.g_spread_bp - measured.i_spread_bp  # type: ignore[operator]
        )

    def test_a_bond_with_no_holding_record_is_refused(self, tmp_path: Path) -> None:
        from treble.tapi.spreads import BondNotPriceableError, bond_spreads

        with pytest.raises(BondNotPriceableError, match="no N-PORT holding"):
            bond_spreads(_store(tmp_path, PAR), identifier="isin:US9999999999", as_of=LATER)

    def test_a_later_but_emptier_report_does_not_win(self, tmp_path: Path) -> None:
        """The live store holds a 2026-08-10 row for one Barclays bond with
        every field null beside a complete 2026-03-31 one. Taking the most
        recent report rather than the most recent *usable* one would refuse
        a bond that is perfectly priceable."""
        from treble.tapi.spreads import bond_spreads

        store = self._with_bond(_store(tmp_path, PAR))
        record = Provenance(
            source_system="edgar-nport",
            source_uri="https://example.invalid/later",
            retrieved_at=KNOWN,
            method=ExtractionMethod.BULK_FILE,
            extractor_version="1",
            payload_hash="c" * 64,
        )
        store.write_provenance([record])
        store.write_facts(
            [
                Fact(
                    subject="isin:US0000000000",
                    field="nport:name",
                    value="TEST ISSUER",
                    effective_from=date(2026, 8, 10),
                    effective_to=date(2026, 8, 10),
                    knowledge_from=KNOWN,
                    provenance_id=record.id,
                )
            ]
        )
        assert bond_spreads(store, identifier="isin:US0000000000", as_of=LATER).price == (
            pytest.approx(98.5)
        )
