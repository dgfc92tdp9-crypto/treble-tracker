"""Fail if a bound cell sits under a period heading without declaring it.

The fourth structural gate, and it exists because `BoundCell.period_from`
was opt-in and opt-in is discipline rather than mechanism (CLAUDE.md §1).

The defect it generalises: `DES` and `FA` rendered Apple's **Q4 FY2018**
revenue — 62,900,000,000 from `us-gaap:Revenues:USD`, a tag Apple abandoned
in 2018 — directly beneath a heading reading "3 months to 2026-03-28", which
came from a different field entirely. The two numbers were seven years apart
and read as one quarter, implying a 47% net margin for a company that runs
about 26%.

`period_from` fixes any cell it is written on. This makes writing it
non-optional, so the next screen cannot reintroduce the defect by omission.

**The rule is positional, because reading is positional.** A `period` cell
states a period for everything laid out beneath it, so a bound cell at or
below a period cell's row must say which period governs it. Cells *above*
the first period heading are not under one — `DES` puts shares outstanding
and public float there — and are left alone rather than forced to name a
period they do not sit under.

A cell bound to the governing field itself needs no declaration: it is
where the period came from.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from treble.render.contract.registry import available, get_screen  # noqa: E402


def main() -> int:
    problems: list[str] = []
    checked = governed = 0

    for mnemonic in sorted(available()):
        for tab in get_screen(mnemonic).tabs:
            periods = [c for c in tab.cells if getattr(c, "kind", None) == "period"]
            if not periods:
                continue
            first_period_row = min(c.at.row for c in periods)
            governing = {c.field for c in periods}
            for cell in tab.cells:
                if getattr(cell, "kind", None) != "bound":
                    continue
                if cell.at.row < first_period_row:
                    continue  # above every heading; not under one
                if cell.field in governing:
                    continue  # it is the source of the period
                checked += 1
                if cell.period_from is None:
                    problems.append(
                        f"  {mnemonic}/{tab.name} row {cell.at.row}: {cell.field} sits under a "
                        "period heading and does not declare period_from"
                    )
                else:
                    governed += 1

    for line in problems:
        print(line, file=sys.stderr)
    if problems:
        print(
            f"screen periods: {len(problems)} bound cell(s) under a period heading with no "
            "period_from. A value from another period would render as though it belonged "
            "to the heading above it — see scripts/check_screen_periods.py.",
            file=sys.stderr,
        )
        return 1
    print(f"screen periods: {governed}/{checked} bound cells under a heading declare their period")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
