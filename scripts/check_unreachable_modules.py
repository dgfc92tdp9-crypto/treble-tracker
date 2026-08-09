"""Fail if a module under treble/ is imported by no other module under treble/.

The third structural gate, and it exists because the first two could not see
this. `check_module_coverage.py` catches a module with no tests;
`check_unread_members.py` catches a field nobody reads. Neither catches a
module that is thoroughly tested and never called by the application —
which is what `analytics/tval/evaluate.py` was: TVAL Prong 1, the evaluated
price with its ASC 820 level, tested and unreachable, so the network could
take contributions and could score observations and could not do both.

**A module here is not dead code.** Most are leaf capabilities: a transport
chosen at deployment, an adapter run out of band, a pricer a screen will
call. The gate does not claim they are worthless. It claims that "nothing
imports this" is a fact worth writing down, because the alternative is
finding out the way I did — a field with no reader, three layers away.

So `ALLOWED_UNREACHABLE` is a backlog, not a suppression. Every entry says
which it is: a genuine entry point, or a capability still waiting on the
wiring that would make it usable. The second kind should shrink.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Entry points: invoked from outside the package, so nothing imports them.
ENTRY_POINTS: dict[str, str] = {
    "treble.tapi.flight": "Arrow Flight server, started by the CLI as a process.",
    "treble.tapi.grpc_server": "gRPC server, started by the CLI as a process.",
    "treble.plant.natsjs": "Transport chosen at deployment, not at import.",
    "treble.plant.kafka": "Transport chosen at deployment, not at import.",
    "treble.ingest.frenchdata": "Adapter run out of band by scripts/backfill_port.py.",
    "treble.ingest.twelvedata": "Adapter run out of band by scripts/backfill_port.py.",
    "treble.ingest.trace": "Adapter run out of band against a downloaded TRACE file.",
}

#: Built, tested, and not yet reachable from a screen or service. This is
#: the backlog. Each line is work someone can pick up, and the reason says
#: what wiring is missing rather than pretending none is.
AWAITING_WIRING: dict[str, str] = {
    "treble.analytics.credit.cds": (
        "CDSW's pricer, validated against ISDA's published grids across six currencies. "
        "Blocked on data, not on a screen: the store holds zero credit subjects and no "
        "spread facts, so a CDSW screen would render empty. Probed 2026-08-07 -- the "
        "DTCC CFTC tape serves CFTC_CUMULATIVE_RATES_<date>.zip (200) but the analogous "
        "CREDIT filename 403s, and pddata.dtcc.com's cumulative listing API returns "
        "empty for RATES and 400 for other asset-class tokens, so it cannot be used to "
        "discover the real name. Finding the credit slice is the next step, and it is a "
        "data question."
    ),
    "treble.analytics.derivatives.crosscurrency": (
        "§12.1 pricer. Not data-blocked: ECB spot, EUR-ESTR-OIS and USD-SOFR-OIS "
        "are all stored and current. Needs build_swap_market taught to build a "
        "second curve under USD conventions; the basis is a caller input."
    ),
    "treble.analytics.derivatives.totalreturn": "§12.1 pricer; no screen calls it yet.",
    "treble.analytics.tval.residual": (
        "TVAL §15.4. explained_residual_bp exists and no evaluated-price path calls it."
    ),
    "treble.tapi.products": (
        "§12.1 product pricing off the stored curves. Gives capfloor and cms their "
        "first callers; the SWPM product tab is what will call this."
    ),
    "treble.tapi.equity_ratios": (
        "§14.1 ratios from stored XBRL. Gives analytics/equity/ratios.py its first "
        "caller; the FA drill-down is what will bind this."
    ),
    "treble.tapi.documents": "The docs service; no screen binds it yet.",
    "treble.tapi.evaluated": "Contributed-price evaluation; no screen binds it yet.",
    "treble.render.layout": (
        "Layout authoring; the desktop shell's drag and resize gestures are the caller "
        "and are not built."
    ),
}

ALLOWED_UNREACHABLE = {**ENTRY_POINTS, **AWAITING_WIRING}


def main() -> int:
    src = [p for p in sorted(REPO.glob("treble/**/*.py")) if "_generated" not in str(p)]
    modules: dict[str, Path] = {}
    for path in src:
        parts = list(path.relative_to(REPO).with_suffix("").parts)
        if parts[-1] == "__init__":
            continue
        modules[".".join(parts)] = path

    imported: set[str] = set()
    for path in src:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{a.name}" for a in node.names)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)

    unreachable = sorted(n for n in modules if n not in imported)
    new = [n for n in unreachable if n not in ALLOWED_UNREACHABLE]
    stale = [n for n in ALLOWED_UNREACHABLE if n not in unreachable]

    for name in new:
        print(
            f"unreachable modules: {name} is imported by no other treble module. Wire it "
            "to a caller, or add it to ENTRY_POINTS or AWAITING_WIRING with the reason.",
            file=sys.stderr,
        )
    for name in stale:
        print(
            f"unreachable modules: {name} now has a caller; remove its entry.",
            file=sys.stderr,
        )
    if new or stale:
        return 1
    print(
        f"unreachable modules: none new "
        f"({len(ENTRY_POINTS)} entry points, {len(AWAITING_WIRING)} awaiting wiring)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
