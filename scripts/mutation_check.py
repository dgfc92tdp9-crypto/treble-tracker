"""Targeted mutation testing, without the tool that fights this project.

Mutation testing answers the only question coverage cannot: *would a test
notice if the code were wrong?* 88% line coverage says the lines run.

**Why not mutmut.** Recorded since 2026-07-27 and re-confirmed: mutmut 3.x
works by synthesising mutated functions at runtime, and this project's
invariant tests work by reflecting over its own code — the I3 registry walk,
the append-only protocol check, the log-API check. The synthesised functions
look like real ones to a reflection test, so mutmut's mutants are killed by
tests that never executed the mutated line. Five such collisions were
diagnosed. They are structural, not configuration.

**What this does instead.** It edits the source text of one module, runs the
tests that cover it, and restores. No synthesis, no import hooks, nothing for
a reflection test to mistake for real code. Slower per mutant and far smaller
in scope — and it produces a number that can be trusted, which the tool did
not.

Scoped to `hagan_west.py`: pure numerics, no reflection, and the module where
a surviving mutant means a wrong *interest rate* rather than a wrong log
line. Each mutation below is a plausible mistake, not a random operator flip
— a boundary that should be strict, a sign, a clamp, an epsilon.

    python scripts/mutation_check.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "treble" / "analytics" / "curves" / "hagan_west.py"
TESTS = ROOT / "tests" / "analytics" / "curves"


@dataclass(frozen=True)
class Mutation:
    """One plausible mistake, and what it would mean if it survived."""

    name: str
    before: str
    after: str
    consequence: str


MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        "positivity clamp lower bound",
        "f[0] = min(max(f[0], 0.0), 2.0 * fds[0])",
        "f[0] = min(f[0], 2.0 * fds[0])",
        "negative forwards at the short end would pass through unclamped",
    ),
    Mutation(
        "positivity clamp upper bound",
        "f[i] = min(max(f[i], 0.0), 2.0 * min(fds[i - 1], fds[i]))",
        "f[i] = max(f[i], 0.0)",
        "the amelioration that keeps the forward monotone would be gone",
    ),
    Mutation(
        "terminal node extrapolation sign",
        "f[n] = fds[n - 1] - 0.5 * (f[n - 1] - fds[n - 1])",
        "f[n] = fds[n - 1] + 0.5 * (f[n - 1] - fds[n - 1])",
        "the long end of every curve would bend the wrong way",
    ),
    Mutation(
        "interval weighting swapped",
        "f[i] = (left_width / (left_width + right_width)) * fds[i] + (",
        "f[i] = (right_width / (left_width + right_width)) * fds[i] + (",
        "unequal spacing would weight the wrong neighbour, skewing every node",
    ),
    Mutation(
        "discrete forward denominator",
        "fds.append((r * t - prev_rt) / (t - prev_t))",
        "fds.append((r * t - prev_rt) / t)",
        "forwards would be wrong wherever tenors are not one year apart",
    ),
    Mutation(
        "epsilon guard widened",
        "_EPS = 1e-16",
        "_EPS = 1e-2",
        "the g-function would take its degenerate branch on real inputs",
    ),
)


def run_tests() -> bool:
    """True if the suite passes. Output discarded: only the code matters."""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", str(TESTS), "-x", "-q", "--no-cov", "-p", "no:randomly"],
        capture_output=True,
        cwd=ROOT,
        check=False,
    )
    return result.returncode == 0


def main() -> int:
    original = TARGET.read_text()

    if not run_tests():
        print("the suite fails before any mutation; fix that first")
        return 2

    with tempfile.TemporaryDirectory() as backup_dir:
        backup = Path(backup_dir) / "original.py"
        shutil.copy2(TARGET, backup)
        survivors: list[Mutation] = []
        try:
            for mutation in MUTATIONS:
                if mutation.before not in original:
                    print(f"  SKIP  {mutation.name}: source no longer contains the target")
                    survivors.append(mutation)  # a mutation that cannot apply proves nothing
                    continue
                TARGET.write_text(original.replace(mutation.before, mutation.after, 1))
                killed = not run_tests()
                TARGET.write_text(original)
                print(f"  {'KILLED' if killed else 'SURVIVED'}  {mutation.name}")
                if not killed:
                    survivors.append(mutation)
        finally:
            # Restore from the backup, not from memory: an interrupt midway
            # must not leave a mutated module on disk.
            shutil.copy2(backup, TARGET)

    print()
    score = (len(MUTATIONS) - len(survivors)) / len(MUTATIONS)
    print(f"killed {len(MUTATIONS) - len(survivors)}/{len(MUTATIONS)}  ({score:.0%})")
    for mutation in survivors:
        print(f"  survivor: {mutation.name} — {mutation.consequence}")
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
