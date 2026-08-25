"""Fail if the data directory is carrying reclaimable waste.

The fourth structural gate. The other three are about code — a module with
no tests, a field with no reader, a module nothing imports. This one is
about the *working copy*, and it exists because a correct, tested,
well-documented compaction routine that nobody runs is worth exactly as
much as one that does not exist.

What it would have caught, on the day it was written: a 335.7 MB database
holding 0.76 MB of facts, and two hand-made 336 MB copies of it beside
them. 1,007 MB of pure waste next to 668 MB of real data, on a laptop with
9 GB free. Nothing was broken. Every component behaved as designed.

**This check skips in CI, and says so.** A fresh checkout has no `data/`,
so there is nothing to measure and nothing to fail on — which makes the
skip path the one that runs almost everywhere, and a skip that printed
nothing would be indistinguishable from a pass. That distinction is the
whole point: this repository has already shipped a guard whose condition
never matched, and the lesson recorded was to make the inert case loud.

The thresholds live in `treble.store.storage.verdict`, which is pure and
tested against constructed reports in `tests/store/test_storage.py` —
including tests that assert it *fails*. A gate whose failure path has
never executed is not a gate.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from treble.cmd.paths import default_data_dir  # noqa: E402
from treble.store.storage import measure, verdict, waste_limit  # noqa: E402


def main() -> int:
    data_dir = default_data_dir()
    if not data_dir.exists():
        # Loud, not silent: see the module docstring. This is the CI path.
        print(f"storage budget: skipped — no data directory at {data_dir}")
        return 0

    report = measure(data_dir)
    if not report.components:
        print(f"storage budget: skipped — {data_dir} is empty")
        return 0

    result = verdict(report)
    if result.ok:
        print(
            f"storage budget: {result.summary} "
            f"({len(report.components)} components, "
            f"{report.size / 1024 / 1024:,.1f} MB total)"
        )
        return 0

    print(f"storage budget: FAILED — {result.summary}", file=sys.stderr)
    print(f"\n{data_dir}:", file=sys.stderr)
    for component in sorted(report.components, key=lambda c: -c.size):
        flag = "  <-- waste" if component.waste else ""
        print(
            f"  {component.size / 1024 / 1024:>10,.1f} MB  {component.name}{flag}",
            file=sys.stderr,
        )
    print("\nto fix:", file=sys.stderr)
    for reason in result.reasons:
        print(f"  - {reason}", file=sys.stderr)
    print(
        f"\n(budget is {waste_limit() / 1024 / 1024:,.0f} MB of reclaimable space; "
        "set TREBLE_WASTE_LIMIT_BYTES to change it)",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
