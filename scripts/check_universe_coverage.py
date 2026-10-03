"""Fail if a configured equity symbol holds nothing and nobody said why.

The fifth structural gate, and the first about the *universe* rather than
the code or the working copy's size. It exists because five symbols sat
permanently outstanding in `populate` and the only thing that noticed was a
non-zero exit nobody was reading: DFS, HES and EA had been delisted, AVB and
EQR are not served by this tier, and all five had been written into the
config from recollection on the day they were added.

**Offline, and deliberately not the same check as `treble coverage`.** That
command asks the vendor what it serves, needs a network and a credential,
and is the thing to run *before* a populate. This asks a different question
with neither: of the symbols this universe claims, which produced no facts?
A symbol can be in the vendor's catalogue and still hold nothing here —
because the populate was interrupted, or rate-limited, or never run — and
that hole is invisible until a screen renders dashes for it.

`equity_tickers_unavailable` is the documented exception, which is why it had
to become data. While it was a YAML comment this check could not have been
written: a symbol removed because the vendor 404s it looked exactly like one
somebody forgot.

**The skip path is loud**, following `check_storage_budget.py`. A fresh
checkout has no store, so skipping is what happens almost everywhere, and a
silent skip is indistinguishable from a pass. This repository has already
shipped a guard whose condition never matched; the lesson recorded was to
make the inert case say so.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from treble.cmd.paths import default_data_dir  # noqa: E402
from treble.core.universe import load_universe_config  # noqa: E402
from treble.store.duck import DuckStore  # noqa: E402

CONFIG = REPO / "config" / "universe.yaml"


def main() -> int:
    data_dir = default_data_dir()
    database = data_dir / "treble.db"
    if not database.exists():
        print(f"universe coverage: skipped — no store at {database}")
        return 0

    config = load_universe_config(CONFIG)
    store = DuckStore(database)
    now = datetime.now(UTC)
    held = {s.split(":", 1)[1] for s in store.subjects_with_prefix("equity:", as_of=now)}

    if not held:
        # Before the first equity populate every symbol is missing, which is
        # a correct state and not a drift. Saying so beats a silent pass.
        print("universe coverage: skipped — the store holds no equity subjects yet")
        return 0

    failures: list[str] = []
    checked = 0
    for name, spec in config.universes.items():
        if not spec.equity_tickers:
            continue
        checked += len(spec.equity_tickers)
        for symbol in spec.equity_tickers:
            if symbol in held:
                continue
            if symbol in spec.equity_tickers_unavailable:
                # Configured *and* excluded. The loader allows it and the
                # coverage tests forbid it; named here too, because a symbol
                # in both places will never be fetched and never be reported.
                failures.append(
                    f"{name}: {symbol} is in equity_tickers and equity_tickers_unavailable"
                )
                continue
            failures.append(f"{name}: {symbol} holds no facts and is not listed as unavailable")

    if not failures:
        excluded = sum(len(s.equity_tickers_unavailable) for s in config.universes.values())
        print(
            f"universe coverage: {checked} configured equity symbols all hold facts "
            f"({excluded} documented as unavailable)"
        )
        return 0

    print(f"universe coverage: FAILED — {len(failures)} symbol(s)", file=sys.stderr)
    for failure in failures[:20]:
        print(f"  - {failure}", file=sys.stderr)
    if len(failures) > 20:
        print(f"  … and {len(failures) - 20} more", file=sys.stderr)
    print("\nto fix:", file=sys.stderr)
    print("  - run `treble coverage` to ask the vendor which it serves", file=sys.stderr)
    print("  - then `treble populate --only twelvedata` for the rest", file=sys.stderr)
    print(
        "  - or record it in `equity_tickers_unavailable` with the reason, "
        "which is what stops it being retried forever",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
