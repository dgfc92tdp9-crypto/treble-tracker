"""I7's kill-test: the import contracts themselves (CLAUDE.md §1).

`lint-imports` enforces the layering, and the gate runs it. But a mechanism
that can be switched off silently is not a mechanism: deleting the contracts
from `.importlinter` leaves `lint-imports` passing with nothing to check,
and every other test would stay green.

This is the missing half — it asserts the configuration still says what it
is supposed to say. Found by the Phase 1 gate audit, which is what an audit
is for.
"""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

CONFIG = Path(__file__).resolve().parents[1] / ".importlinter"


@pytest.fixture(scope="module")
def contracts() -> dict[str, dict[str, str]]:
    parser = configparser.ConfigParser()
    parser.read(CONFIG)
    return {
        parser[section].get("name", section): dict(parser[section])
        for section in parser.sections()
        if section.startswith("importlinter:contract")
    }


def test_the_config_exists() -> None:
    assert CONFIG.is_file(), "the layering is enforced by this file; it must exist"


def test_both_contracts_are_declared(contracts: dict[str, dict[str, str]]) -> None:
    """Two contracts, and losing either would go unnoticed otherwise."""
    assert len(contracts) == 2, f"expected 2 contracts, found {sorted(contracts)}"


class TestTapiIsTheOnlyDataPath:
    """I7 proper: presentation code must not reach past the API."""

    def test_the_forbidden_contract_exists(self, contracts: dict[str, dict[str, str]]) -> None:
        assert any(c.get("type") == "forbidden" for c in contracts.values())

    @pytest.mark.parametrize("module", ["treble.screens", "treble.render"])
    def test_presentation_modules_are_constrained(
        self, contracts: dict[str, dict[str, str]], module: str
    ) -> None:
        forbidden = next(c for c in contracts.values() if c.get("type") == "forbidden")
        assert module in forbidden["source_modules"]

    @pytest.mark.parametrize("module", ["treble.store", "treble.analytics", "treble.ingest"])
    def test_the_data_layers_are_forbidden_to_them(
        self, contracts: dict[str, dict[str, str]], module: str
    ) -> None:
        forbidden = next(c for c in contracts.values() if c.get("type") == "forbidden")
        assert module in forbidden["forbidden_modules"]


class TestLayeringIsDeclaredInOrder:
    def test_the_layers_contract_exists(self, contracts: dict[str, dict[str, str]]) -> None:
        assert any(c.get("type") == "layers" for c in contracts.values())

    def test_every_layer_is_present_and_ordered(self, contracts: dict[str, dict[str, str]]) -> None:
        """The order is the architecture. Reordering two entries would let a
        lower layer import a higher one with nothing objecting."""
        layers = next(c for c in contracts.values() if c.get("type") == "layers")
        declared = [line.strip() for line in layers["layers"].splitlines() if line.strip()]
        assert declared == [
            "treble.render",
            "treble.screens",
            "treble.tapi",
            "treble.tql",
            "treble.analytics",
            "treble.store",
            "treble.core",
        ]


def test_the_gate_actually_runs_the_linter() -> None:
    """A correct config that nothing executes enforces nothing."""
    gate = (Path(__file__).resolve().parents[1] / "scripts" / "gate.sh").read_text()
    assert "lint-imports" in gate
