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
        """The check that would have caught the original mistake.

        Compared against the ledger's *own* two-decimal formatting rather
        than against the raw float within a tolerance. The tolerance version
        read `abs=0.005` and failed for any figure landing exactly on a
        rounding tie: at 46.875 the correctly-rounded 46.88 sits
        0.0050000000000026 away, which is outside a bound of 0.005. Deriving
        one from the other removes the boundary instead of widening it, and
        is stricter — it admits exactly one string.
        """
        assert stated_in_progress() == float(compute().format().rstrip("%"))

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


class TestEveryPhaseIsCreditedProRata:
    """Advancing the gate must not credit work that was never done.

    The computation used to credit earlier phases their *full* weight, on the
    stated assumption that "earlier phases are complete by definition of the
    phase gates". That assumption fails the moment a phase is gated with a
    criterion legitimately short of 1.0 — and Phase 2 will be, because
    P2_4's ratings and P2_8's execution venue are blocked outside this
    repository rather than unbuilt.

    Moving `active_phase` to phase_3 would have taken the figure from 54.94%
    to 55.00% with no commit in between.
    """

    @staticmethod
    def _ledger() -> dict[str, object]:
        return {
            "weights": {"phase_1": 50, "phase_2": 50},
            "active_phase": "phase_1",
            "phase_1": {"A": {"done": 1.0, "name": "a"}},
            # Deliberately short of 1.0, as Phase 2 will be at its gate.
            "phase_2": {"B": {"done": 0.5, "name": "b", "basis": "half"}},
        }

    def test_an_incomplete_earlier_phase_is_not_rounded_up(self) -> None:
        from scripts.completion import compute

        ledger = self._ledger()
        ledger["active_phase"] = "phase_2"
        before = compute(ledger).overall
        # Advance the gate without changing a single `done`.
        ledger["active_phase"] = "phase_1"
        after = compute(ledger).overall
        assert before == pytest.approx(75.0)
        assert after == pytest.approx(before), "the figure moved without any work"

    def test_the_figure_is_the_weighted_sum_of_every_phase(self) -> None:
        """Stated as arithmetic so the property is checkable by hand rather
        than by rerunning the implementation."""
        from scripts.completion import compute

        assert compute(self._ledger()).overall == pytest.approx(50 * 1.0 + 50 * 0.5)

    def test_a_weighted_phase_with_no_entries_is_refused(self) -> None:
        """A phase carrying weight and listing no deliverables makes the
        figure a statement about an unwritten plan. Phases 3-5 were exactly
        that until they were broken down."""
        from scripts.completion import LedgerError, compute

        ledger = self._ledger()
        del ledger["phase_2"]
        with pytest.raises(LedgerError, match="no ledger entries"):
            compute(ledger)


class TestPhasesThreeToFiveAreFullyEnumerated:
    def test_every_gate_criterion_has_an_entry(self) -> None:
        """One entry per criterion in CLAUDE.md §8 — 5, 4 and 5. A phase
        missing a criterion would report a completion it had not earned the
        denominator for."""
        from scripts.completion import load_ledger

        ledger = load_ledger()
        assert len(ledger["phase_3"]) == 5
        assert len(ledger["phase_4"]) == 4
        assert len(ledger["phase_5"]) == 5

    def test_each_names_what_blocks_it(self) -> None:
        """The blocker kinds need different responses and read identically
        as prose. `unverified` is the honest option and must stay available:
        P3_3 uses it because nobody has checked whether a free FIX simulator
        exists, and recording that as `code` would be a guess dressed as an
        assessment."""
        from scripts.completion import load_ledger

        ledger = load_ledger()
        allowed = {"code", "data", "terms", "cost", "unverified"}
        for phase in ("phase_3", "phase_4", "phase_5"):
            for name, entry in ledger[phase].items():
                assert entry["blocker"] in allowed, (name, entry.get("blocker"))

    def test_any_progress_states_what_was_counted(self) -> None:
        """This asserted every Phase 3-5 entry was 0.0, which was true when
        written and stopped being true when P3_3 reached 0.5.

        The replacement is the property actually worth holding: a partial
        anywhere must carry a `basis`. It also found a real gap — the ledger
        validator checked only the *active* phase, so a partial in Phase 3
        while Phase 2 was active needed no basis at all. A guess in an
        inactive phase is exactly as much a guess.
        """
        from scripts.completion import load_ledger

        ledger = load_ledger()
        for phase in ("phase_3", "phase_4", "phase_5"):
            for name, entry in ledger[phase].items():
                if 0.0 < entry["done"] < 1.0:
                    assert entry.get("basis"), f"{name} is partial with no basis"

    def test_the_validator_rejects_a_partial_with_no_basis_in_any_phase(self) -> None:
        """The gap the test above uncovered, pinned so it cannot return."""
        import copy

        import yaml

        from scripts.completion import LEDGER, LedgerError, load_ledger

        raw = yaml.safe_load(LEDGER.read_text())
        broken = copy.deepcopy(raw)
        broken["phase_5"]["P5_5"] = {"done": 0.5, "name": "Mobile"}
        path = Path(__file__).parent / "_broken_ledger.yaml"
        path.write_text(yaml.safe_dump(broken))
        try:
            with pytest.raises(LedgerError, match="needs a `basis`"):
                load_ledger(path)
        finally:
            path.unlink()
