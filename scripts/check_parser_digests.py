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

#: Adapters with no recorded digest, and why.
#:
#: **Empty, and that is the state to keep it in.** Every shipped adapter
#: records a digest of its parse over a recorded fixture, so a parser that
#: changes what it produces without changing `parser_version` fails the
#: build rather than being found later in the store.
#:
#: Two entries lived here briefly on the reasoning that `edgar-bulk` and
#: `gleif-isin` "need recorded parse config, so a digest would pin one
#: universe rather than the parser". That was wrong: the config in a fixture
#: test is *fixed*, so the digest changes if and only if the parser does,
#: which is exactly what is wanted. A third, `trace-api`, was excused for
#: never having been fetched — but it has a recorded fixture, and a parser
#: guarded only once real data arrives is unguarded for precisely the run
#: that first writes rows.
UNGUARDED: dict[str, str] = {}


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
    outstanding = "" if not UNGUARDED else f", {len(UNGUARDED)} outstanding with a reason"
    print(f"parser digests: {len(recorded)} of {len(shipped)} adapters guarded{outstanding}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
