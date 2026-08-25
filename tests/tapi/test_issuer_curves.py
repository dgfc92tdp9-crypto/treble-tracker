"""Issuer curves are fitted over corporate debt, not securitisations.

This module had no tests at all, which is how the defect below survived: it
sat at 0% coverage while the repository-wide floor passed comfortably. A
coverage floor is an average, and an average cannot notice one module at
zero.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tests.storebuilder import split_holding
from treble.core.facts import Fact
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.issuer_curves import CORPORATE_ASSET_CATEGORIES, build_issuer_curves

DAY = date(2026, 6, 30)
KNOWN = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
LEI = "5493001KJTIIGC8Y1R12"


def _write(store: DuckStore, holdings: list[tuple[str, str, date, float, float]]) -> None:
    """Each holding: (isin, assetCat, maturity, coupon, value)."""
    provenance = Provenance(
        source_system="edgar-nport",
        source_uri="https://example.invalid/primary_doc.xml",
        retrieved_at=KNOWN,
        method=ExtractionMethod.DOCUMENT,
        extractor_version="1",
        payload_hash="0" * 64,
    )
    facts: list[Fact] = []
    for isin, asset_cat, maturity, coupon, value in holdings:
        for subject, field, value_ in split_holding(
            f"isin:{isin}",
            {
                "nport:lei": LEI,
                "nport:assetCat": asset_cat,
                "nport:issuerCat": "CORP",
                "nport:name": "Example Issuer",
                "nport:curCd": "USD",
                "nport:maturityDt": maturity,
                "nport:annualizedRt": coupon,
                "nport:valUSD": value,
                "nport:balance": 1_000_000.0,
            },
        ):
            facts.append(
                Fact(
                    subject=subject,
                    field=field,
                    value=value_,
                    effective_from=DAY,
                    effective_to=DAY,
                    knowledge_from=KNOWN,
                    provenance_id=provenance.id,
                )
            )
    store.write_provenance([provenance])
    store.write_facts(facts)


@pytest.fixture
def store(tmp_path: Path) -> DuckStore:
    return DuckStore(tmp_path / "t.db")


def _corporate(isin: str, maturity: date, coupon: float) -> tuple[str, str, date, float, float]:
    return (isin, "DBT", maturity, coupon, 990_000.0)


class TestOnlyCorporateDebtIsFitted:
    """The filer's own `assetCat`, not `issuerCat`.

    `issuerCat` cannot separate these and reading it as though it could was
    the defect: a securitisation vehicle *is* a corporation, so filers
    classify it CORP. On the live store 90% of debt holdings read
    issuerCat=CORP while assetCat put 171 of 460 bonds — 37%, across 36
    LEIs — in ABS-O, ABS-MBS, ABS-CBDO or STIV. Applying this filter took
    the fitted set from 35 curves to 27.
    """

    def test_a_securitisation_is_excluded_with_its_reason(self, store: DuckStore) -> None:
        _write(
            store,
            [
                _corporate("US0000000AA1", date(2029, 6, 30), 0.045),
                _corporate("US0000000AB9", date(2032, 6, 30), 0.050),
                _corporate("US0000000AC7", date(2035, 6, 30), 0.053),
                ("US0000000AD5", "ABS-CBDO", date(2033, 6, 30), 0.075, 940_000.0),
            ],
        )
        built = build_issuer_curves(store, as_of=datetime.now(UTC))
        reasons = dict(built.excluded)
        assert "isin:US0000000AD5" in reasons
        assert "ABS-CBDO" in reasons["isin:US0000000AD5"]
        assert "isin:US0000000AA1" not in reasons

    def test_the_excluded_bond_does_not_reach_the_curve(self, store: DuckStore) -> None:
        """The point of the exclusion, not merely that it is reported.

        A CLO tranche's spread is about its collateral pool and its place in
        that deal's waterfall. Averaged into an issuer curve it moves the
        fit, and the screen then reports rich/cheap against a number that is
        partly a statement about somebody else's receivables.
        """
        _write(
            store,
            [
                _corporate("US0000000AA1", date(2029, 6, 30), 0.045),
                _corporate("US0000000AB9", date(2032, 6, 30), 0.050),
                _corporate("US0000000AC7", date(2035, 6, 30), 0.053),
                ("US0000000AD5", "ABS-CBDO", date(2033, 6, 30), 0.075, 940_000.0),
            ],
        )
        built = build_issuer_curves(store, as_of=datetime.now(UTC))
        fitted = {bond for curve in built.curves.values() for bond in curve.bonds}
        assert "isin:US0000000AD5" not in fitted
        assert len(fitted) == 3

    def test_the_allowed_set_is_corporate_debt_only(self) -> None:
        """Guards the constant itself.

        Widening it to include an ABS category would silently undo both
        tests above, because they would then be asserting on a bond the
        filter admits.
        """
        assert frozenset({"DBT"}) == CORPORATE_ASSET_CATEGORIES
