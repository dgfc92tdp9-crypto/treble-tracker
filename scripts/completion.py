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

    for name, entry in data[phase].items():
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

    # Earlier phases are complete by definition of the phase gates; the
    # active phase contributes its own weight pro rata.
    weights = data["weights"]
    ordered = sorted(weights)
    earlier = sum(weights[name] for name in ordered if name < phase)
    overall = earlier + fraction * weights[phase]

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
