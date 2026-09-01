"""What the data directory is using, and how much of it is waste.

Written after the store reached 1.2 GB on a 245 GB laptop that was 96%
full, and the cause turned out to be entirely mechanical: `treble compact`
exists, is correct, moves 21x more data than it leaves behind — and is
manual. Nothing ran it, so the hot tier regrew after every ingest and
stayed there. Alongside it sat two 336 MB copies of the database that an
earlier session had made by hand before a risky migration and never
removed.

Neither was a bug. Both were the absence of a mechanism, which is why the
remedy here is a *measurement* and a *gate*, not a cleanup script: a
cleanup script fixes today, and the gate is what stops the next six months
of the same thing.

**The split in this module is deliberate.** :func:`measure` touches the
disk and reports; :func:`verdict` is a pure function of a report and a
budget. The gate asserts on `verdict`, so the thresholds can be tested
against constructed reports rather than against whatever happens to be on
the developer's disk that day — which is the difference between a check
with tests and a check that has only ever been observed to pass.

Nothing here deletes anything. `waste` names bytes a *documented, lossless*
command would return, and the command is named in `remedy` so the report
tells you what to run rather than doing it behind your back.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import duckdb

#: Files that are a copy of the database taken by hand. Nothing in this
#: repository writes one — these are made by people and by agents before a
#: migration, and the two that prompted this module were 672 MB between
#: them and eight hours stale. They are waste by definition: the database
#: they copy is still there, and if it were not, the copy is a *worse*
#: recovery path than the payload store (see the module docstring in
#: `cold.py` on why the derived store is not the thing worth protecting).
BACKUP_SUFFIXES = (".bak", ".backup", ".old", ".orig", ".copy")

#: Left by a compaction that died between writing and renaming. `cold.py`
#: keeps them out of reads by name, so one costs disk and nothing else —
#: but it also means a compaction failed, which is worth surfacing.
PARTIAL_SUFFIX = ".compacting"

#: Total reclaimable bytes tolerated before the gate refuses. Absolute
#: rather than a fraction: a fraction of a small store is a small number,
#: and the incident this exists to prevent was 1,007 MB of waste beside
#: 668 MB of real data — a ratio that no percentage threshold flags as
#: unusual because the waste was *larger* than the payload.
#:
#: 256 MB is roughly one uncompacted ingest cycle. Below it, running
#: `compact` is not yet worth the Parquet rewrite; above it, the store is
#: carrying a full cycle it never cleaned up.
DEFAULT_WASTE_LIMIT = 256 * 1024 * 1024

#: Override for a machine with different headroom. Read at call time, not
#: at import: a module-level default bound at import cannot be changed by a
#: test or by the environment the gate actually runs in, which is a mistake
#: this repository has already made once with `MANDATE_DIR`.
WASTE_LIMIT_ENV = "TREBLE_WASTE_LIMIT_BYTES"


def waste_limit() -> int:
    """The configured ceiling, or the default.

    A value that does not parse is an error rather than a silent fallback:
    `TREBLE_WASTE_LIMIT_BYTES=512MB` quietly meaning "the default" would
    make the gate pass for a reason the operator did not choose.
    """
    raw = os.environ.get(WASTE_LIMIT_ENV)
    if raw is None or raw == "":
        return DEFAULT_WASTE_LIMIT
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{WASTE_LIMIT_ENV}={raw!r} is not an integer number of bytes") from exc
    if value < 0:
        raise ValueError(f"{WASTE_LIMIT_ENV}={value} must not be negative")
    return value


#: Hot rows above which an ingest compacts before it finishes.
#:
#: The gate catches a bloated store before a commit; this is what keeps it
#: from bloating in the first place, because the store grows when data is
#: ingested and nobody commits after every ingest. Without it the gate
#: would be a tripwire that fires long after the damage, which is what the
#: manual `compact` already was.
#:
#: 1,000,000 is a little under the 1,953,485 rows the hot tier had reached
#: when this was written — so the condition would have been true well
#: before the store hit 336 MB, and is false on a freshly compacted one.
HOT_ROW_LIMIT = 1_000_000


def maintenance_due(hot_rows: int, *, limit: int = HOT_ROW_LIMIT) -> bool:
    """Whether the hot tier has grown enough to be worth compacting.

    Pure, and a separate decision from :func:`verdict`: that one asks
    whether waste has *already* accumulated, this asks whether an ingest
    should clean up after itself. A threshold rather than "always" because
    compaction rewrites whole Parquet partitions — 19.7s for two million
    rows — and paying that after an ingest that added a few hundred facts
    would teach people to reach for a flag that skips it.
    """
    return hot_rows > limit


@dataclass(frozen=True)
class Component:
    """One thing in the data directory, its size, and what of it is waste."""

    name: str
    path: Path
    size: int
    waste: int = 0
    remedy: str | None = None

    def __post_init__(self) -> None:
        if self.waste > self.size:
            raise ValueError(f"{self.name}: waste {self.waste} exceeds size {self.size}")
        if self.waste and not self.remedy:
            raise ValueError(f"{self.name}: {self.waste} bytes of waste with no remedy named")


@dataclass(frozen=True)
class StorageReport:
    """Everything under the data directory, measured."""

    root: Path
    components: tuple[Component, ...]

    @property
    def size(self) -> int:
        return sum(c.size for c in self.components)

    @property
    def waste(self) -> int:
        return sum(c.waste for c in self.components)

    @property
    def wasteful(self) -> tuple[Component, ...]:
        """Components carrying waste, largest first — the fix list."""
        return tuple(sorted((c for c in self.components if c.waste), key=lambda c: -c.waste))


@dataclass(frozen=True)
class Verdict:
    """Whether the store is within budget, and why not if it isn't."""

    ok: bool
    waste: int
    limit: int
    reasons: tuple[str, ...]

    @property
    def summary(self) -> str:
        if self.ok:
            return f"{_mb(self.waste)} reclaimable, within the {_mb(self.limit)} budget"
        return f"{_mb(self.waste)} reclaimable, over the {_mb(self.limit)} budget"


def _mb(value: int) -> str:
    return f"{value / 1024 / 1024:,.1f} MB"


def verdict(report: StorageReport, *, limit: int | None = None) -> Verdict:
    """Whether ``report`` is within budget. Pure — no disk, no clock.

    A partial compaction file fails regardless of size. It is small, so a
    byte budget would never catch it, and it means a compaction died
    partway — which is worth knowing about while the cause is still
    recent rather than the next time someone reads the directory listing.
    """
    ceiling = waste_limit() if limit is None else limit
    reasons: list[str] = []

    partials = [c for c in report.components if c.path.name.endswith(PARTIAL_SUFFIX)]
    for partial in partials:
        reasons.append(f"{partial.name}: a compaction did not finish; {partial.remedy}")

    if report.waste > ceiling:
        for component in report.wasteful:
            reasons.append(f"{component.name}: {_mb(component.waste)} — {component.remedy}")

    return Verdict(ok=not reasons, waste=report.waste, limit=ceiling, reasons=tuple(reasons))


def _tree_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _is_backup(path: Path) -> bool:
    """A hand-made copy of something, by name.

    Matches `treble.db.bak-20260825-090507` as well as `treble.db.bak`,
    because the timestamped form is the one people actually write and a
    check that only caught the bare suffix would have missed both files
    from the incident that prompted this.
    """
    name = path.name
    return any(marker in name for marker in BACKUP_SUFFIXES)


def free_list_bytes(db_path: Path) -> int:
    """Blocks the database has allocated from the filesystem and is not using.

    `CHECKPOINT` does not return these and neither does deleting rows —
    only rebuilding the file does, which is what `DuckStore.reclaim` is
    for. Measured on the live store: 937 used blocks out of 1343, in a
    335.7 MB file holding facts that fit in 0.76 MB once rebuilt.

    Opens read-only so measuring can never be the thing that corrupts the
    store, and returns 0 rather than raising if the file is locked by a
    running workstation — a gate that fails because the TUI is open would
    teach people to skip the gate.
    """
    if not db_path.exists():
        return 0
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
    except duckdb.Error:
        return 0
    try:
        row = conn.execute("PRAGMA database_size").fetchone()
        if row is None:  # pragma: no cover - the pragma always returns a row
            return 0
        columns = [d[0] for d in conn.description or ()]
        fields = dict(zip(columns, row, strict=False))
        total, used = fields.get("total_blocks"), fields.get("used_blocks")
        size = fields.get("block_size")
        if not isinstance(total, int) or not isinstance(used, int) or not isinstance(size, int):
            return 0
        return max(0, (total - used) * size)
    except duckdb.Error:
        return 0
    finally:
        conn.close()


def measure(data_dir: Path) -> StorageReport:
    """Walk the data directory and account for every top-level entry.

    Every entry is reported, including ones with no waste, because the
    question this answers is "what is using the space" and a report that
    listed only problems could not answer it.
    """
    if not data_dir.exists():
        return StorageReport(root=data_dir, components=())

    components: list[Component] = []
    for entry in sorted(data_dir.iterdir()):
        size = _tree_size(entry)
        if entry.name.endswith(PARTIAL_SUFFIX):
            components.append(
                Component(
                    name=entry.name,
                    path=entry,
                    size=size,
                    waste=size,
                    remedy="delete it and re-run `treble compact`",
                )
            )
        elif _is_backup(entry):
            components.append(
                Component(
                    name=entry.name,
                    path=entry,
                    size=size,
                    waste=size,
                    remedy="a hand-made copy; delete it once the live store verifies",
                )
            )
        elif entry.name == "treble.db":
            free = min(free_list_bytes(entry), size)
            components.append(
                Component(
                    name=entry.name,
                    path=entry,
                    size=size,
                    waste=free,
                    remedy="run `treble compact --reclaim`" if free else None,
                )
            )
        else:
            components.append(Component(name=entry.name, path=entry, size=size))
    return StorageReport(root=data_dir, components=tuple(components))


#: Days of headroom below which the data directory is reported as a
#: problem rather than a number.
#:
#: Measured on this machine, 2026-09-01: the payload store alone would take
#: **24.15 GB/year** if every source were fetched at the cadence it
#: declares, against **8.1 GB free**. Two GLEIF bulk files account for 23.3
#: GB of that — 37 MB and 27 MB per fetch, both declared daily. Nothing was
#: wrong; nothing had run them daily yet. The projection is what the disk
#: does the moment somebody turns updates up, which is the stated goal.
#:
#: 180 days rather than 30, because the remedy is not quick: it is
#: negotiating a cadence down, or moving the data directory, or switching a
#: bulk source to deltas. A warning that arrives with a month left is a
#: warning that arrives too late to do any of those calmly.
RUNWAY_FLOOR_DAYS = 180.0


@dataclass(frozen=True)
class Growth:
    """Projected bytes per day, and what is producing them.

    Separate from :class:`StorageReport` because it answers a different
    question. That one asks what is on the disk now and what of it is
    waste; this asks what will be on the disk later. The incident that
    prompted `measure` was waste already accumulated — this is the one
    where nothing is wasted and the disk fills anyway.
    """

    per_day: int
    #: (source id, bytes/day), largest first. Named, because "24 GB a year"
    #: is not actionable and "gleif-rr, 37 MB every day" is.
    contributors: tuple[tuple[str, int], ...] = ()

    @property
    def per_year(self) -> int:
        return self.per_day * 365


def runway_days(free_bytes: int, growth: Growth) -> float | None:
    """Days until projected growth exhausts ``free_bytes``.

    ``None`` when nothing is growing — distinct from a very large number,
    because "no declared cadence anywhere" and "centuries of headroom" want
    different responses and would otherwise render the same.
    """
    if growth.per_day <= 0:
        return None
    return free_bytes / growth.per_day


def free_bytes(path: Path) -> int:
    """Space available on the filesystem holding ``path``.

    The filesystem's number, not the volume's total minus the store: other
    things share this disk, and on the machine this was written for they
    are 97% of it.
    """
    usage = shutil.disk_usage(path if path.exists() else path.parent)
    return usage.free


@dataclass(frozen=True)
class RunwayVerdict:
    """Whether the headroom is enough for the declared cadences.

    Its own type rather than a reused :class:`Verdict`. That one measures
    megabytes against a byte budget and its ``summary`` says so; filling it
    with zeroes to carry a *time* would print "0.0 MB reclaimable, within
    the 0.0 MB budget" beside a warning that the disk has four months left.
    """

    ok: bool
    #: None when nothing is growing — see :func:`runway_days`.
    days: float | None
    floor_days: float
    reasons: tuple[str, ...]

    @property
    def summary(self) -> str:
        if self.days is None:
            return "no source has both a declared cadence and a stored payload"
        return f"{self.days:,.0f} days of headroom against a {self.floor_days:,.0f}-day floor"


def runway_verdict(
    free: int, growth: Growth, *, floor_days: float = RUNWAY_FLOOR_DAYS
) -> RunwayVerdict:
    """Whether there is enough headroom for the declared cadences. Pure.

    Deliberately **not** part of :func:`verdict` and deliberately not a gate
    failure: waste is something to clean up today, and runway is a plan to
    revise. Failing a commit over a projection would teach people to set the
    override and stop reading either one.
    """
    days = runway_days(free, growth)
    if days is None or days >= floor_days:
        return RunwayVerdict(ok=True, days=days, floor_days=floor_days, reasons=())
    reasons = [
        f"{_mb(growth.per_day)}/day projected against {_mb(free)} free — "
        f"{days:,.0f} days of headroom, under the {floor_days:,.0f}-day floor"
    ]
    reasons.extend(f"{source}: {_mb(per_day)}/day" for source, per_day in growth.contributors[:5])
    return RunwayVerdict(ok=False, days=days, floor_days=floor_days, reasons=tuple(reasons))


__all__ = [
    "BACKUP_SUFFIXES",
    "DEFAULT_WASTE_LIMIT",
    "HOT_ROW_LIMIT",
    "PARTIAL_SUFFIX",
    "RUNWAY_FLOOR_DAYS",
    "WASTE_LIMIT_ENV",
    "Component",
    "Growth",
    "RunwayVerdict",
    "StorageReport",
    "Verdict",
    "free_bytes",
    "free_list_bytes",
    "maintenance_due",
    "measure",
    "runway_days",
    "runway_verdict",
    "verdict",
    "waste_limit",
]
