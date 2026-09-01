"""What the declared cadences will cost the disk.

`store.storage.measure` answers "what is on the disk and what of it is
waste". This answers the question that one cannot: **what will be on the
disk a year from now if every source runs as often as it says it does.**

The distinction matters because the two failures look nothing alike. The
incident behind `storage.py` was 1 GB of reclaimable waste beside 668 MB of
real data — something to clean up. This one has no waste at all: every
payload is content-addressed, immutable, and the substrate I5 replay is
built on. Nothing here is deletable, and the disk fills anyway.

Measured on this machine, 2026-09-01:

    gleif-rr        37.27 MB/fetch    declared daily     13.60 GB/yr
    gleif-isin      26.64 MB/fetch    declared daily      9.72 GB/yr
    edgar-bulk      97.15 MB/fetch    declared 92-daily   0.39 GB/yr
    dtcc-sdr         1.01 MB/fetch    declared daily      0.37 GB/yr
    ------------------------------------------------------------
    total (declared cadences only)                       24.15 GB/yr
                                            free on this disk: 8.1 GB

Nothing was broken and nothing had gone wrong: those two GLEIF sources had
been fetched three times each, ever, because nothing schedules a refresh.
The projection is what happens the day somebody turns updates up — which is
the thing everyone wants from a data workstation, and is exactly when a
laptop with 8 GB free stops working.

**Estimated from what has actually been fetched**, not from documentation:
the mean size of the distinct payloads this store holds per source, times
the cadence the adapter declares. A source that has never been fetched
contributes nothing rather than a guess, and says so.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from pathlib import Path

from treble.ingest.registry import all_sources
from treble.store.ingest_log import IngestLog
from treble.store.storage import Growth


def payload_sizes(payload_root: Path) -> dict[str, int]:
    """Every stored payload, by content hash, with its size on disk."""
    if not payload_root.exists():
        return {}
    return {p.stem: p.stat().st_size for p in payload_root.rglob("*") if p.is_file()}


def project(log: IngestLog, payload_root: Path) -> Growth:
    """Bytes per day the declared cadences imply, and who is producing them.

    The mean of a source's *distinct* payloads rather than of its log
    entries. A source fetched ten times that returned the same bytes twice
    stored one file, and counting the log entry twice would inflate it by
    the amount the content-addressed store just saved.
    """
    sizes = payload_sizes(payload_root)
    seen: dict[str, set[str]] = defaultdict(set)
    for entry in log.read():
        if entry.payload_hash in sizes:
            seen[entry.source].add(entry.payload_hash)

    meta = all_sources()
    contributors: list[tuple[str, int]] = []
    for source, hashes in seen.items():
        cadence = getattr(meta.get(source), "expected_cadence_days", None)
        if not cadence or not hashes:
            # No declared cadence means staleness is not judged for this
            # source (`health.py`), and projecting one here would invent
            # the expectation that module deliberately refuses to invent.
            continue
        mean = statistics.mean(sizes[h] for h in hashes)
        contributors.append((source, int(mean / cadence)))

    contributors.sort(key=lambda pair: -pair[1])
    return Growth(
        per_day=sum(per_day for _, per_day in contributors),
        contributors=tuple(contributors),
    )


__all__ = ["payload_sizes", "project"]
