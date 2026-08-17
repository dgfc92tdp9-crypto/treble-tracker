"""Compute the reported completion percentage from the ledger.

The figure used to be written by hand, and a single update got it wrong
twice: the phase weight was reverse-engineered from an earlier reported
number instead of read from the model, and partial work packages were
credited by impression rather than by deliverable.

Neither was an arithmetic error. Both were the same mistake — preferring a
recalled value to the recorded one — so the compensation is to remove the
opportunity to recall. Nobody writes the number now; this computes it from
`config/completion.yaml`, and `tests/test_completion.py` fails the gate if
PROGRESS.md disagrees.

    python scripts/completion.py           # 20.42%
    python scripts/completion.py --verbose # the full derivation
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

LEDGER = Path(__file__).resolve().parents[1] / "config" / "completion.yaml"
PROGRESS = Path(__file__).resolve().parents[1] / "PROGRESS.md"


class LedgerError(Exception):
    """The ledger is not self-consistent. Never silently tolerated: a
    ledger that cannot be trusted is worse than no ledger, because the
    number it produces still looks authoritative."""


@dataclass(frozen=True)
class Completion:
    overall: float
    phase_fraction: float
    phase: str
    completed: float
    total: int

    def format(self) -> str:
        return f"{self.overall:.2f}%"


def load_ledger(path: Path = LEDGER) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text())
    weights = data["weights"]

    total_weight = sum(weights.values())
    if total_weight != 100:
        raise LedgerError(f"phase weights sum to {total_weight}, not 100")

    phase = data["active_phase"]
    if phase not in weights:
        raise LedgerError(f"active_phase {phase!r} has no weight")

    # Every phase, not just the active one. This checked `data[phase]` alone,
    # which was harmless while the other phases held nothing — and stopped
    # being harmless the moment Phase 3 took a partial value while Phase 2
    # was still active. A partial in an inactive phase is exactly as capable
    # of being a guess as one in the active phase, and was exactly as
    # unchecked.
    for phase_name in weights:
        for name, entry in data.get(phase_name, {}).items():
            done = entry["done"]
            if not 0.0 <= done <= 1.0:
                raise LedgerError(f"{name}: done={done} is outside [0, 1]")
            # A partial must say what was counted. Without this the ledger
            # would just relocate the guesswork rather than remove it.
            if 0.0 < done < 1.0 and not entry.get("basis"):
                raise LedgerError(f"{name}: partial completion needs a `basis`")
    return data


def compute(data: dict[str, Any] | None = None) -> Completion:
    data = data or load_ledger()
    phase = data["active_phase"]
    packages = data[phase]

    completed = sum(entry["done"] for entry in packages.values())
    total = len(packages)
    fraction = completed / total

    # Every phase contributes its weight pro rata, including the ones behind
    # the active gate.
    #
    # This used to credit earlier phases their *full* weight, on the stated
    # assumption that "earlier phases are complete by definition of the phase
    # gates". That assumption is false the moment a phase is gated with a
    # criterion legitimately short of 1.0 — and Phase 2 will be, because
    # P2_4's ratings and P2_8's execution venue are blocked outside this
    # repository rather than unbuilt. Advancing `active_phase` to phase_3
    # would have moved the figure from 54.94% to 55.00% with no work done:
    # a silent gift of the 0.06 those two criteria never earned.
    #
    # Found while breaking Phases 3-5 into ledger items, which is what put
    # every phase in the file and made the uniform form possible. It is
    # behaviour-preserving today — Phase 1 is 16 entries all at 1.0, so its
    # 30 points are earned either way — and it stops the figure crediting
    # work that was never done later.
    weights = data["weights"]
    overall = 0.0
    for name, weight in weights.items():
        entries = data.get(name)
        if not entries:
            raise LedgerError(
                f"{name} has a weight of {weight} and no ledger entries. A phase that "
                "carries weight and lists no deliverables makes the figure a statement "
                "about an unwritten plan"
            )
        overall += weight * sum(e["done"] for e in entries.values()) / len(entries)

    return Completion(
        overall=overall,
        phase_fraction=fraction * 100,
        phase=phase,
        completed=completed,
        total=total,
    )


def main() -> int:
    result = compute()
    if "--verbose" in sys.argv:
        data = load_ledger()
        for name, entry in data[data["active_phase"]].items():
            marker = "x" if entry["done"] == 1.0 else " "
            print(f"  [{marker}] {name:<5} {entry['done']:>4}  {entry['name']}")
        print()
        print(f"  {result.completed:g} of {result.total} work packages")
        print(f"  {result.phase} at {result.phase_fraction:.2f}%")
    print(result.format())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
