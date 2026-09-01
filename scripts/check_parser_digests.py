"""Fail if a shipped adapter has no recorded parse digest.

The fifth structural gate, and it exists because three adapters changed what
they produced while keeping their `parser_version` — `dtcc-sdr` (227 against
234 for one payload), `sec-nport` (two subject schemes under one version) and
`openfigi` (an effective date that moved with the fetch). I5 states that a
parser is a pure function of (payload, parser version); nothing enforced it,
and each breach was found only after the wrong rows were in the store.

`tests/ingest/test_parser_output_is_stable.py` is the enforcement, and a test
only guards the adapters that call it. This is what stops a new adapter — or
an old one nobody got to — from being outside it silently.

`UNGUARDED` is a backlog, not a suppression. Every entry says why, and it
should shrink.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from treble.ingest.replay import adapter_classes  # noqa: E402

DIGESTS = REPO / "tests" / "ingest" / "parser_digests.json"

#: Adapters with no recorded digest yet, and the reason.
UNGUARDED: dict[str, str] = {
    "edgar-bulk": (
        "Needs recorded parse config (the filer set), so a digest would pin one "
        "universe rather than the parser. Guard it with a fixed config alongside "
        "the fixture."
    ),
    "gleif-isin": (
        "Needs recorded parse config (the requested ISINs) for the same reason as edgar-bulk."
    ),
    "edgar-submissions": "Fixture test exists; digest not wired yet.",
    "frenchdata": "Fixture test exists; digest not wired yet.",
    "gleif": "Per-LEI record lookup, four payloads on the live store; not wired yet.",
    "coinbase-products": "Fixture test exists; digest not wired yet.",
    "trace-api": "Never fetched — awaiting a FINRA credential, so nothing to record.",
}


def main() -> int:
    if not DIGESTS.is_file():
        print("parser digests: FAILED — no recorded digests at all", file=sys.stderr)
        return 1
    recorded = set(json.loads(DIGESTS.read_text()))
    shipped = {c.meta.source_id for c in adapter_classes().values() if hasattr(c, "meta")}

    missing = sorted(shipped - recorded - set(UNGUARDED))
    stale = sorted(set(UNGUARDED) & recorded)
    unknown = sorted(set(UNGUARDED) - shipped)

    for source in missing:
        print(
            f"parser digests: {source} has no recorded digest. Call "
            "`check_parser_digest` from its fixture test, or add it to UNGUARDED "
            "with the reason.",
            file=sys.stderr,
        )
    for source in stale:
        print(
            f"parser digests: {source} is guarded now; remove its UNGUARDED entry.", file=sys.stderr
        )
    for source in unknown:
        print(f"parser digests: UNGUARDED names {source}, which ships no adapter.", file=sys.stderr)
    if missing or stale or unknown:
        return 1
    print(
        f"parser digests: {len(recorded)} of {len(shipped)} adapters guarded "
        f"({len(UNGUARDED)} outstanding, each with a reason)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
