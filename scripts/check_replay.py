"""Ingest, replay, compare — the I5 round trip, run nightly.

ADR-0010 measured a full replay of the live store once: 13.8 million facts
from 488 payloads, 86.2% of the live store reproduced, every remaining fact
accounted for. A measurement taken once is a fact about that afternoon. This
is the part that keeps it true.

## What it checks, and why that claim is exact

Seed a store from the recorded fixtures through the **real adapters** — the
same `payload store -> ingest log -> parse` ordering `run()` uses — then
replay the log into a second store and require the two to be **identical on
all twelve fact columns**.

No tolerances and no allowances, because this comparison has no reason to
need any. Both stores come from the same bytes and the same parser version
in the same process, so anything but equality is a defect:

  - a `parse` reading the wall clock, so two runs disagree on knowledge time;
  - a provenance id that is not a pure function of its fields, so
    `provenance_id` drifts and every fact with it;
  - a `parse` reading configuration that `parse_config` does not record,
    which is the defect ADR-0010 fixed and this is the guard against its
    return;
  - dict or set iteration order leaking into output.

The live-store comparison in ADR-0010 needed allowances — superseded parser
output, renamed sources — because it compared today's parsers against a store
built over a year. **That comparison cannot be a gate**, and not because it
is slow: its allowances would have to be edited every time a parser is
corrected, and a check whose expected answer is edited to match the observed
one has stopped checking. This one is exact and stays exact.

## Why nightly rather than per-commit

Seeding runs real adapters over 7.9 MB of fixtures and the replay runs them
again. That is seconds, not minutes — but the per-commit suite already
exercises replay for three adapters synthetically, and this is the broader,
slower, end-to-end version. `make gate` stays fast; this runs at 03:17.

Exits non-zero on any difference, naming the source.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from treble.cmd.seed import seed, seed_available  # noqa: E402
from treble.ingest.replay import rebuild  # noqa: E402
from treble.store.compare import StoreComparison  # noqa: E402
from treble.store.duck import DuckStore  # noqa: E402
from treble.store.ingest_log import IngestLog  # noqa: E402
from treble.store.payloads import PayloadStore  # noqa: E402

CONTACT = "replay-check@treble.invalid"


def main() -> int:
    if not seed_available():
        # Loud, never silent. The fixtures are committed, so their absence
        # means a checkout problem rather than a clean skip — and a check
        # that prints nothing when it does nothing is indistinguishable
        # from one that passed.
        print("replay check: FAILED — recorded fixtures are missing", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payloads = PayloadStore(root / "payloads")
        log = IngestLog(root / "ingest.db")
        original = DuckStore(root / "original.db")

        written = seed(payloads, log, original, contact_email=CONTACT)
        entries = len(log.read())
        print(f"seeded {written:,} facts from {entries} recorded payload(s)")
        if not written or not entries:
            print("replay check: FAILED — seeding produced nothing to replay", file=sys.stderr)
            return 1

        replayed = DuckStore(root / "replayed.db")
        report = rebuild(replayed, payloads, log)
        print(f"replayed {report.facts:,} facts from {report.entries} payload(s), no network")

        if report.unclaimed:
            print(
                f"replay check: FAILED — no adapter for {', '.join(report.unclaimed)}",
                file=sys.stderr,
            )
            return 1
        for source, seq, message in report.failures:
            print(f"replay check: FAILED — {source} seq {seq}: {message}", file=sys.stderr)
        if report.failures:
            return 1
        for degraded in report.unconfigured:
            # **This cannot fire today, and saying so is the point.** Both
            # seeded adapters parse their bytes and nothing else, so
            # `needs_config` is false for each and the counter stays zero
            # whatever `seed` records. It is here for the day a
            # config-needing adapter joins the seed set — at which point a
            # `seed` that forgot to record its config would otherwise
            # produce a store that replays to something different and
            # compares clean, because both halves would be equally wrong.
            #
            # Written down rather than left as a plausible-looking guard: a
            # branch whose condition never matches reads exactly like one
            # that has never had reason to.
            print(
                f"replay check: FAILED — {degraded.source} replayed without "
                f"recorded parse config ({degraded.unconfigured} of {degraded.entries})",
                file=sys.stderr,
            )
            return 1

        # Both handles must be released before the comparison can attach
        # them: DuckDB allows one handle per file per process.
        original.close()
        replayed.close()
        return _compare(root / "original.db", root / "replayed.db")


def _compare(original: Path, replayed: Path) -> int:
    with StoreComparison(original, replayed) as comparison:
        results = comparison.compare_all()

    if not results:
        print("replay check: FAILED — nothing to compare", file=sys.stderr)
        return 1

    failed = [r for r in results if not r.exact]
    for result in results:
        mark = "ok" if result.exact else "DIFFERS"
        print(
            f"  {result.source:<24} {result.left:>9,} -> {result.right:>9,}  "
            f"{mark} ({result.verdict})"
        )

    if failed:
        print(
            f"\nreplay check: FAILED — {len(failed)} source(s) did not reproduce exactly",
            file=sys.stderr,
        )
        for result in failed:
            print(
                f"  {result.source}: {result.only_right:,} fact(s) only in the replay, "
                f"{result.only_left:,} only in the original",
                file=sys.stderr,
            )
        return 1

    total = sum(r.left for r in results)
    print(f"\nreplay check: {total:,} facts reproduced exactly across {len(results)} source(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
