"""The completion figure must be computed, never asserted.

An update stated the percentage from memory and got it wrong twice: the
phase weight was reverse-engineered from an earlier (also wrong) figure
rather than read, and partial work packages were credited by impression
rather than by deliverable. Neither was arithmetic — both were preferring
a recalled value to the recorded one.

These tests close that off. PROGRESS.md cannot state a number the ledger
does not produce, and the ledger cannot hold a partial without saying what
was counted.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from completion import LEDGER, PROGRESS, LedgerError, compute, load_ledger

STATED = re.compile(r"\*\*Completion:\s*([0-9]+\.[0-9]{2})%\*\*")


def stated_in_progress() -> float:
    match = STATED.search(PROGRESS.read_text())
    assert match is not None, "PROGRESS.md must state **Completion: N.NN%**"
    return float(match.group(1))


class TestProgressMatchesTheLedger:
    def test_stated_figure_equals_the_computed_one(self) -> None:
        """The check that would have caught the original mistake."""
        assert stated_in_progress() == pytest.approx(compute().overall, abs=0.005)

    def test_figure_is_quoted_to_two_decimals(self) -> None:
        # Jack asked for 2 dp specifically, so the format is part of the
        # contract rather than a presentational detail.
        assert STATED.search(PROGRESS.read_text()) is not None

    def test_script_prints_what_progress_states(self) -> None:
        """End-to-end, because the test importing `compute` directly could
        agree with PROGRESS.md while the script a human runs does not."""
        result = subprocess.run(  # noqa: S603
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "scripts" / "completion.py"),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == f"{stated_in_progress():.2f}%"


class TestLedgerIsSelfConsistent:
    def test_weights_sum_to_one_hundred(self) -> None:
        assert sum(yaml.safe_load(LEDGER.read_text())["weights"].values()) == 100

    def test_phase_one_has_sixteen_work_packages(self) -> None:
        # WP0 to WP15. A miscount here silently rescales every future figure.
        assert len(load_ledger()["phase_1"]) == 16

    def test_partial_without_a_basis_is_rejected(self, tmp_path: Path) -> None:
        """The rule that stops the ledger from relocating the guesswork
        instead of removing it."""
        ledger = tmp_path / "completion.yaml"
        ledger.write_text(
            "weights: {phase_1: 30, phase_2: 25, phase_3: 15, phase_4: 20, phase_5: 10}\n"
            "active_phase: phase_1\n"
            "phase_1:\n"
            "  WP0: {done: 0.5, name: 'no basis given'}\n"
        )
        with pytest.raises(LedgerError, match="needs a `basis`"):
            load_ledger(ledger)

    def test_impossible_fraction_is_rejected(self, tmp_path: Path) -> None:
        ledger = tmp_path / "completion.yaml"
        ledger.write_text(
            "weights: {phase_1: 30, phase_2: 25, phase_3: 15, phase_4: 20, phase_5: 10}\n"
            "active_phase: phase_1\n"
            "phase_1:\n"
            "  WP0: {done: 1.5, name: 'over-complete'}\n"
        )
        with pytest.raises(LedgerError, match="outside"):
            load_ledger(ledger)

    def test_weights_that_do_not_sum_are_rejected(self, tmp_path: Path) -> None:
        ledger = tmp_path / "completion.yaml"
        ledger.write_text(
            "weights: {phase_1: 30, phase_2: 25}\nactive_phase: phase_1\nphase_1: {}\n"
        )
        with pytest.raises(LedgerError, match="sum to"):
            load_ledger(ledger)

    def test_every_partial_states_what_was_counted(self) -> None:
        for name, entry in load_ledger()["phase_1"].items():
            if 0.0 < entry["done"] < 1.0:
                assert entry.get("basis"), f"{name} is partial without a basis"
