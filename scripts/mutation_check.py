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

Scoped to the pure numerical modules: no reflection, and a surviving mutant
means a wrong *number* rather than a wrong log line. Each mutation below is
a plausible mistake, not a random operator flip — a boundary that should be
strict, a sign, a clamp, a curve read from the wrong place.

Targets:

- `hagan_west.py` — the interpolation every curve is built on.
- `swap.py` — `SWPM`. Its mutations are the four ways a multi-curve pricer
  silently reverts to the single-curve world, each of which produces a
  plausible PV and no error.

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


@dataclass(frozen=True)
class Mutation:
    """One plausible mistake, and what it would mean if it survived."""

    name: str
    before: str
    after: str
    consequence: str


@dataclass(frozen=True)
class Target:
    """One module, the tests that cover it, and its plausible mistakes."""

    path: Path
    tests: Path
    mutations: tuple[Mutation, ...]

    @property
    def name(self) -> str:
        return self.path.name


CURVE_MUTATIONS: tuple[Mutation, ...] = (
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

# Every one of these produces an ordinary-looking PV. None raises, none
# returns a NaN, and none would be caught by a coverage figure — which is
# exactly why the multi-curve pricer is worth mutating rather than trusting.
SWAP_MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        "float leg discounted off the forecast curve",
        '''discount_factor = discount.discount_at(end)
        flows.append(
            Cashflow(
                leg="float"''',
        '''discount_factor = forecast.discount_at(end)
        flows.append(
            Cashflow(
                leg="float"''',
        "the pre-2008 single-curve collapse: the floating leg telescopes and "
        "the basis vanishes from every long-dated PV",
    ),
    Mutation(
        "forwards projected off the discount curve",
        "growth = forecast.discount_at(start) / forecast.discount_at(end) - 1.0",
        "growth = discount.discount_at(start) / discount.discount_at(end) - 1.0",
        "the index would be projected at the collateral rate, understating "
        "every floating coupon by the basis",
    ),
    Mutation(
        "float schedule taken from the trade rather than the curve",
        "dates = _schedule(spec, forecast.config.index_frequency)",
        "dates = _schedule(spec, spec.fixed_frequency)",
        "a 3M index read on a semiannual schedule: every forward taken over the wrong period",
    ),
    Mutation(
        "fixed leg accrues on the floating convention",
        "accrual = _accrual(spec.fixed_day_count, start, end)",
        "accrual = _accrual(spec.float_day_count, start, end)",
        "30/360 against ACT/360 is a 1.4% error on every fixed coupon",
    ),
    Mutation(
        "notional read at the period end",
        "notional = spec.notional_at(start)",
        "notional = spec.notional_at(end)",
        "an amortising schedule would step one period early, changing every "
        "coupon around each step",
    ),
    Mutation(
        "risk shifts one curve instead of rebuilding the set",
        "bumped = _value(spec, curves.bumped(1.0), csa)",
        "bumped = _value(spec, curves.bumped(1.0, curve=spec.forecast_curve), csa)",
        "DV01 would omit the discount curve's contribution entirely",
    ),
)

TARGETS: tuple[Target, ...] = (
    Target(
        path=ROOT / "treble" / "analytics" / "curves" / "hagan_west.py",
        tests=ROOT / "tests" / "analytics" / "curves",
        mutations=CURVE_MUTATIONS,
    ),
    Target(
        path=ROOT / "treble" / "analytics" / "derivatives" / "swap.py",
        tests=ROOT / "tests" / "analytics" / "derivatives",
        mutations=SWAP_MUTATIONS,
    ),
)


def run_tests(tests: Path) -> bool:
    """True if the suite passes. Output discarded: only the code matters."""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", str(tests), "-x", "-q", "--no-cov", "-p", "no:randomly"],
        capture_output=True,
        cwd=ROOT,
        check=False,
    )
    return result.returncode == 0


def check(target: Target) -> list[Mutation]:
    """Apply every mutation to one target; return the survivors."""
    print(f"\n{target.name}  ({len(target.mutations)} mutations)")
    original = target.path.read_text()
    survivors: list[Mutation] = []
    with tempfile.TemporaryDirectory() as backup_dir:
        backup = Path(backup_dir) / "original.py"
        shutil.copy2(target.path, backup)
        try:
            for mutation in target.mutations:
                if mutation.before not in original:
                    print(f"  SKIP  {mutation.name}: source no longer contains the target")
                    survivors.append(mutation)  # a mutation that cannot apply proves nothing
                    continue
                target.path.write_text(original.replace(mutation.before, mutation.after, 1))
                killed = not run_tests(target.tests)
                target.path.write_text(original)
                print(f"  {'KILLED' if killed else 'SURVIVED'}  {mutation.name}")
                if not killed:
                    survivors.append(mutation)
        finally:
            # Restore from the backup, not from memory: an interrupt midway
            # must not leave a mutated module on disk.
            shutil.copy2(backup, target.path)
    return survivors


def main() -> int:
    for target in TARGETS:
        if not run_tests(target.tests):
            print(f"{target.name}: the suite fails before any mutation; fix that first")
            return 2

    survivors: list[Mutation] = []
    total = 0
    for target in TARGETS:
        total += len(target.mutations)
        survivors.extend(check(target))

    print()
    killed = total - len(survivors)
    print(f"killed {killed}/{total}  ({killed / total:.0%})")
    for mutation in survivors:
        print(f"  survivor: {mutation.name} — {mutation.consequence}")
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
