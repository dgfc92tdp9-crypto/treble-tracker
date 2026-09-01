"""Move the store to another disk, and prove it arrived.

The data directory grows and the disk does not. At the cadences the sources
declare it adds roughly 9 GB a year, against 8.5 GB free on a volume that is
97% full — so the question is not whether it needs somewhere bigger but when.

Moving it is three lines of `mv` and one bad afternoon. What makes it worth
a module is everything around the copy:

**Nothing is deleted until the copy is proved.** Not "the file count
matches" — every payload is read back *through the public path* at the
target, which verifies it against its content address (`PayloadStore.get`),
and the fact count is compared across both stores. Only then does the
source go. An interruption at any point leaves the original intact and the
target incomplete, which is the safe way round.

**The old directory is not left silent.** It gets a pointer naming the new
location, which `cmd.paths` follows. Relocation therefore needs no
configuration: nothing to export and nothing to forget. A store moved to an
external disk with an environment variable holding it together is a store
that reads empty the first time somebody opens a new shell — which is the
same failure `paths.default_data_dir` already exists to prevent, arriving
by a different road.

**Refusal is cheap and late is expensive.** Space, an existing store at the
target, a target inside the source: all checked before a byte moves.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from treble.core.datadir import (
    MARKER_NAME,
    POINTER_NAME,
    holds_data,
    new_identity,
    read_marker,
    write_marker,
    write_pointer,
)

#: Free space to leave at the target beyond what is being copied.
#:
#: The store grows the moment it is used again — one ingest, one compaction
#: rewriting Parquet partitions — and a move that exactly fits is a move
#: onto a disk that is full on arrival. A gigabyte is roughly a compaction's
#: working set on the live store.
HEADROOM_BYTES = 1024**3


class RelocationError(Exception):
    """The move cannot proceed, or did not arrive intact."""


def tree_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


@dataclass(frozen=True)
class RelocationPlan:
    """What a move would do, decided before anything is copied."""

    source: Path
    target: Path
    bytes_to_move: int
    target_free: int
    target_holds_store: bool

    @property
    def problems(self) -> tuple[str, ...]:
        """Every reason to refuse, together. Pure — no disk, no clock.

        All of them at once rather than the first: a person told about the
        space, then about the existing store, then about the nesting, has
        been made to run the command three times to learn one answer.
        """
        reasons: list[str] = []
        if not self.source.is_dir():
            reasons.append(f"there is no store at {self.source}")
        if self.target == self.source:
            reasons.append("the target is the source")
        if self.source in self.target.parents:
            reasons.append(
                f"{self.target} is inside {self.source} — the copy would be copying itself"
            )
        if self.target_holds_store:
            reasons.append(
                f"{self.target} already holds a store; move it aside or choose another path"
            )
        needed = self.bytes_to_move + HEADROOM_BYTES
        if self.target_free < needed:
            reasons.append(
                f"{self.target} has {self.target_free / 1024**3:,.1f} GB free; "
                f"the move needs {self.bytes_to_move / 1024**3:,.1f} GB plus "
                f"{HEADROOM_BYTES / 1024**3:,.0f} GB of headroom"
            )
        return tuple(reasons)

    @property
    def ok(self) -> bool:
        return not self.problems


def plan(source: Path, target: Path) -> RelocationPlan:
    """Measure the move. Touches the disk; decides nothing."""
    source, target = source.resolve(), target.expanduser().resolve()
    anchor = target if target.exists() else target.parent
    while not anchor.exists() and anchor != anchor.parent:
        anchor = anchor.parent
    return RelocationPlan(
        source=source,
        target=target,
        bytes_to_move=tree_size(source),
        target_free=shutil.disk_usage(anchor).free,
        target_holds_store=target.is_dir() and holds_data(target),
    )


def _payload_hashes(data_dir: Path) -> list[str]:
    """Every payload the ingest log references, without importing the log's
    module into a hot loop — the log is a DuckDB file and this is the only
    query needed."""
    import duckdb

    log_path = data_dir / "ingest.db"
    if not log_path.exists():
        return []
    conn = duckdb.connect(str(log_path), read_only=True)
    try:
        rows = conn.execute("SELECT DISTINCT payload_hash FROM ingest_log").fetchall()
        return [str(r[0]) for r in rows]
    finally:
        conn.close()


def _fact_count(data_dir: Path) -> int:
    import duckdb

    db = data_dir / "treble.db"
    if not db.exists():
        return 0
    conn = duckdb.connect(str(db), read_only=True)
    try:
        row = conn.execute("SELECT count(*) FROM facts").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def verify_arrival(source: Path, target: Path) -> Iterator[str]:
    """Prove the target is the store, yielding progress as it goes.

    Raises :class:`RelocationError` on the first thing that does not match.

    Payloads are read back **through `PayloadStore.get`**, which verifies
    each against its content address — so this catches a truncated copy, a
    silently dropped file and a corrupted one, which comparing sizes would
    not. They are the irreplaceable half of the store: the derived tables
    can be rebuilt by replaying them, and they cannot be rebuilt from
    anything.
    """
    from treble.store.payloads import PayloadIntegrityError, PayloadStore

    expected = _payload_hashes(source)
    yield f"verifying {len(expected):,} payloads at the target"
    store = PayloadStore(target / "payloads")
    missing: list[str] = []
    for key in expected:
        try:
            store.get(key)  # type: ignore[arg-type]
        except (PayloadIntegrityError, OSError, FileNotFoundError):
            missing.append(key)
            if len(missing) > 3:
                break
    if missing:
        raise RelocationError(
            f"{len(missing)} payload(s) did not arrive intact, including "
            f"{missing[0]}. The source at {source} has not been touched."
        )

    source_facts, target_facts = _fact_count(source), _fact_count(target)
    yield f"verifying {source_facts:,} hot facts"
    if source_facts != target_facts:
        raise RelocationError(
            f"the target holds {target_facts:,} hot facts and the source {source_facts:,}. "
            f"The source at {source} has not been touched."
        )

    for name in ("cold", "ingest.db"):
        if tree_size(source / name) != tree_size(target / name):
            raise RelocationError(
                f"{name} differs in size between {source} and {target}. "
                f"The source at {source} has not been touched."
            )
    yield "verified"


def relocate(
    ready: RelocationPlan,
    *,
    label: str | None = None,
    on_progress: Callable[[str], None] | None = None,
    remove_source: bool = True,
) -> Path:
    """Copy, verify, then leave a pointer behind and remove the source.

    ``remove_source=False`` keeps the original as a second copy. Offered
    because one external disk is capacity, not safety: the payloads are the
    only part of the store that cannot be rebuilt, and a mirror is the
    difference between a dead disk costing an afternoon and costing the
    history.
    """
    say = on_progress or (lambda _: None)
    if not ready.ok:
        raise RelocationError("; ".join(ready.problems))

    say(f"copying {ready.bytes_to_move / 1024**3:,.2f} GB to {ready.target}")
    ready.target.parent.mkdir(parents=True, exist_ok=True)
    # `dirs_exist_ok` so a target the operator created themselves is usable;
    # `plan` has already refused one that holds a store.
    shutil.copytree(ready.source, ready.target, dirs_exist_ok=True)

    for message in verify_arrival(ready.source, ready.target):
        say(message)

    identity = read_marker(ready.target) or new_identity(label)
    write_marker(ready.target, identity)

    if remove_source:
        say(f"removing {ready.source}")
        shutil.rmtree(ready.source)
    else:
        # The pointer must not sit in a directory that still holds a store,
        # or the resolver would follow it away from a perfectly good copy.
        say(f"keeping the original at {ready.source}")
        return ready.target
    write_pointer(ready.source, ready.target, identity)
    say(f"left {POINTER_NAME} at {ready.source}")
    return ready.target


__all__ = [
    "HEADROOM_BYTES",
    "MARKER_NAME",
    "RelocationError",
    "RelocationPlan",
    "plan",
    "relocate",
    "tree_size",
    "verify_arrival",
]
