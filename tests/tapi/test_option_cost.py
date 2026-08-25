"""`OAS1` — the option-cost sensitivity.

The screen exists in this shape because N-PORT publishes no call schedule.
Every row is a conditional, so the tests are about the properties that hold
*whatever* structure is assumed — the ones that would expose a broken
lattice, a mis-signed cost, or a schedule that silently did not apply.

The strongest is the sign. A call right belongs to the issuer, so it can
only narrow the holder's spread: option cost is non-negative for every
structure and every volatility, and a negative one means the model is wrong
rather than the market unusual.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tests.storebuilder import split_holding
from treble.core.facts import Fact
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.option_cost import (
    STRUCTURES,
    VOLATILITIES,
    OptionCostUnavailableError,
    option_cost_grid,
)
from treble.tapi.swap_market import USD_DISCOUNT_CURVE

KNOWN = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
REPORT = date(2026, 3, 31)
CURVE_DAY = date(2026, 3, 31)
SWAP = {"1Y": 0.042, "2Y": 0.043, "3Y": 0.0435, "5Y": 0.044, "7Y": 0.045, "10Y": 0.046}


def _store(
    tmp_path: Path, *, maturity: date = date(2036, 3, 31), price: float = 100.0, name: str = "o"
) -> DuckStore:
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = DuckStore(tmp_path / f"{name}.db")
    record = Provenance(
        source_system="dtcc-sdr",
        source_uri="https://example.invalid/x",
        retrieved_at=KNOWN,
        method=ExtractionMethod.BULK_FILE,
        extractor_version="1",
        payload_hash="a" * 64,
    )
    store.write_provenance([record])
    facts = [
        Fact(
            subject=f"swap:{USD_DISCOUNT_CURVE}:{tenor}",
            field="PAR_RATE",
            value=rate,
            effective_from=CURVE_DAY,
            effective_to=CURVE_DAY,
            knowledge_from=KNOWN,
            provenance_id=record.id,
        )
        for tenor, rate in SWAP.items()
    ]
    bond: dict[str, object] = {
        "nport:maturityDt": maturity,
        "nport:annualizedRt": 5.0,
        "nport:curCd": "USD",
        "nport:assetCat": "DBT",
        "nport:name": "TEST ISSUER",
        "nport:valUSD": 1_000_000.0 * price / 100.0,
        "nport:balance": 1_000_000.0,
    }
    facts += [
        Fact(
            subject=subject,
            field=field,
            value=value,
            effective_from=REPORT,
            effective_to=REPORT,
            knowledge_from=KNOWN,
            provenance_id=record.id,
        )
        for subject, field, value in split_holding("isin:US0000000000", bond)
    ]
    store.write_facts(facts)
    return store


class TestTheSignIsNotNegotiable:
    """A call right belongs to the issuer. It can only narrow the holder's
    spread, so the cost of it can only be positive."""

    def test_every_cell_has_a_non_negative_option_cost(self, tmp_path: Path) -> None:
        grid = option_cost_grid(_store(tmp_path), identifier="isin:US0000000000", as_of=LATER)
        assert grid.rows
        for row in grid.rows:
            assert row.option_cost_bp >= 0.0, (row.structure, row.volatility)

    def test_the_oas_never_exceeds_the_bullet_z(self, tmp_path: Path) -> None:
        """The same statement from the other side, and worth both: a cost
        computed as `z - oas` is non-negative by construction if `z` is
        stale, so this checks the pair rather than the subtraction."""
        grid = option_cost_grid(_store(tmp_path), identifier="isin:US0000000000", as_of=LATER)
        assert all(row.oas_bp <= grid.z_spread_bp + 1e-9 for row in grid.rows)


class TestItRespondsToItsInputs:
    """A grid whose cells did not move with the parameters would be a
    lattice that ignored the schedule — and would look entirely plausible."""

    def test_option_cost_rises_with_volatility(self, tmp_path: Path) -> None:
        grid = option_cost_grid(_store(tmp_path), identifier="isin:US0000000000", as_of=LATER)
        by_structure: dict[str, list[tuple[float, float]]] = {}
        for row in grid.rows:
            by_structure.setdefault(row.structure, []).append((row.volatility, row.option_cost_bp))
        checked = 0
        for cells in by_structure.values():
            costs = [cost for _, cost in sorted(cells)]
            if len(costs) < 2:
                continue
            assert costs == sorted(costs), costs
            checked += 1
        assert checked, "no structure had two volatilities to compare"

    def test_a_wider_call_window_costs_more(self, tmp_path: Path) -> None:
        """Three years of callability is worth more to the issuer than three
        months of it. If the schedule were being ignored these would be
        equal, and every other assertion here would still pass."""
        grid = option_cost_grid(_store(tmp_path), identifier="isin:US0000000000", as_of=LATER)
        top_vol = max(VOLATILITIES)
        costs = {
            row.protection_years: row.option_cost_bp
            for row in grid.rows
            if row.volatility == top_vol
        }
        assert len(costs) >= 2
        widths = sorted(costs)
        assert costs[widths[0]] < costs[widths[-1]]

    def test_the_bullet_row_is_the_bond_as_it_is(self, tmp_path: Path) -> None:
        """The anchor. Every other row is the bond as it is not, and
        without this one on the screen there is nothing to read them
        against."""
        grid = option_cost_grid(_store(tmp_path), identifier="isin:US0000000000", as_of=LATER)
        assert grid.z_spread_bp > 0.0
        assert grid.curve_date == CURVE_DAY


class TestItRefusesRatherThanInvents:
    def test_a_structure_that_does_not_fit_is_skipped_with_a_reason(self, tmp_path: Path) -> None:
        """A bond with eighteen months left has no three-year non-call
        period. Dropping the row silently would read as a structure that
        cost nothing."""
        grid = option_cost_grid(
            _store(tmp_path, maturity=date(2027, 9, 30), name="short"),
            identifier="isin:US0000000000",
            as_of=LATER,
        )
        assert grid.skipped
        assert any("option life" in reason for _, reason in grid.skipped)
        assert all(row.protection_years < 3.0 for row in grid.rows)

    def test_a_bond_with_no_life_left_is_an_error_not_an_empty_grid(self, tmp_path: Path) -> None:
        with pytest.raises(OptionCostUnavailableError, match="no structure priced"):
            option_cost_grid(
                _store(tmp_path, maturity=date(2026, 5, 1), name="tiny"),
                identifier="isin:US0000000000",
                as_of=LATER,
            )

    def test_no_curve_is_an_error_that_says_what_is_missing(self, tmp_path: Path) -> None:
        """Option cost is measured from the bullet Z-spread, which needs a
        discount curve. Without one there is nothing to measure from, and
        an empty grid would not say that."""
        store = DuckStore(tmp_path / "nocurve.db")
        record = Provenance(
            source_system="edgar-nport",
            source_uri="https://example.invalid/n",
            retrieved_at=KNOWN,
            method=ExtractionMethod.BULK_FILE,
            extractor_version="1",
            payload_hash="b" * 64,
        )
        store.write_provenance([record])
        store.write_facts(
            [
                Fact(
                    subject=subject,
                    field=field,
                    value=value,
                    effective_from=REPORT,
                    effective_to=REPORT,
                    knowledge_from=KNOWN,
                    provenance_id=record.id,
                )
                for subject, field, value in split_holding(
                    "isin:US0000000000",
                    {
                        "nport:maturityDt": date(2036, 3, 31),
                        "nport:annualizedRt": 5.0,
                        "nport:curCd": "USD",
                        "nport:assetCat": "DBT",
                        "nport:valUSD": 1_000_000.0,
                        "nport:balance": 1_000_000.0,
                    },
                )
            ]
        )
        with pytest.raises(OptionCostUnavailableError, match="discount curve"):
            option_cost_grid(store, identifier="isin:US0000000000", as_of=LATER)


class TestTheGridIsFullyStated:
    def test_every_structure_and_volatility_is_named(self, tmp_path: Path) -> None:
        """The structure and the vol are columns, not hidden parameters.
        The moment either becomes a default the answer reads as a
        measurement rather than a conditional."""
        grid = option_cost_grid(_store(tmp_path), identifier="isin:US0000000000", as_of=LATER)
        assert {row.volatility for row in grid.rows} == set(VOLATILITIES)
        assert {row.structure for row in grid.rows} <= {label for label, _ in STRUCTURES}
        assert all(row.structure for row in grid.rows)
