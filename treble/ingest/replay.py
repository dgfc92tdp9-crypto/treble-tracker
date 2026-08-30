"""Rebuild a store from stored payloads, without touching the network (I5).

`SourceAdapter.replay` has re-parsed one source's log since Phase 1. What
did not exist was the thing that makes it *mean* something: an orchestrator
that replays every source into a fresh store, and a comparison proving the
result is the store you started with.

Until that existed, "the database is derived and could be rebuilt from the
payloads" was an architectural claim nobody had executed — which is why
sessions kept making 336 MB copies of the database instead of trusting the
604 MB of payloads it derives from. ADR-0008 recorded it as an open item.

## Constructing an adapter to parse but not to fetch

Adapters take fetch configuration in `__init__` — `series`, `ciks`,
`symbols`, `report_dates`, a contact email, an API key. None of it is
needed to parse bytes that were fetched months ago, and inventing values
for it would be guessing: an empty `ciks` tuple is a fine stand-in right up
until some `parse` reads it, at which point the replay quietly produces
different facts and the comparison reports a divergence that looks like a
parser change.

So :func:`parse_only` does not call `__init__` at all. It allocates the
instance and sets exactly the three attributes the base class needs, and
any `parse` that reaches for fetch configuration raises `AttributeError`
naming the attribute. That is the correct outcome: a `parse` depending on
anything but its payload is an I5 violation, and this is the mechanism that
finds them rather than averaging over them.

`ecb-fx` and `edgar-*` were the ones to watch — the latter takes an
`accepted` mapping carrying the EDGAR acceptance time, which is a knowledge
date (I2). See `tests/ingest/test_replay.py` for what each of the nineteen
actually does.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import treble.ingest
from treble.ingest.base import ParsedBatch, RawPayload, SourceAdapter
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore
from treble.store.protocols import FactWriter


def adapter_classes() -> dict[str, type[SourceAdapter]]:
    """Every adapter class by source id, discovered rather than listed.

    Same discipline as `registry.all_sources`, and for the same reason: a
    hand-maintained list would let an adapter added later be silently
    absent from replay, and a replay missing a source produces a store
    that is quietly incomplete — the failure this whole module exists to
    make impossible.
    """
    classes: dict[str, type[SourceAdapter]] = {}
    for info in pkgutil.walk_packages(treble.ingest.__path__, prefix="treble.ingest."):
        module = importlib.import_module(info.name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, SourceAdapter) or obj is SourceAdapter:
                continue
            meta = getattr(obj, "meta", None)
            source_id = getattr(meta, "source_id", None)
            if not isinstance(source_id, str):
                continue
            existing = classes.get(source_id)
            if existing is not None and existing is not obj:
                raise ValueError(
                    f"two adapter classes claim source id {source_id!r}: "
                    f"{existing.__qualname__} and {obj.__qualname__}"
                )
            classes[source_id] = obj
    return classes


def parse_only(cls: type[SourceAdapter], payloads: PayloadStore, log: IngestLog) -> SourceAdapter:
    """An adapter that can `parse` but not `fetch`.

    Deliberately bypasses `__init__` — see the module docstring. The three
    attributes set here are the ones `SourceAdapter` itself relies on;
    `_bucket` is None because throttling is a property of fetching and a
    replay makes no requests.
    """
    adapter = object.__new__(cls)
    adapter._payloads = payloads
    adapter._log = log
    adapter._bucket = None
    return adapter


@dataclass(frozen=True)
class SourceReplay:
    """What replaying one source produced."""

    source: str
    entries: int
    facts: int
    provenance: int
    #: Entries whose parse raised, as (seq, exception). Collected rather
    #: than raised so one broken source cannot hide the other eighteen —
    #: the same reasoning as `refresh`, and the reason this returns a
    #: report instead of just writing.
    failures: tuple[tuple[int, str], ...] = ()

    #: Entries replayed without the configuration `parse` needed, because
    #: they were written before the log recorded any. The facts came out,
    #: but they are what *today's default* produces rather than what the
    #: original run did — a `gleif-isin` entry with no recorded ISIN filter
    #: yields nothing at all. Counted separately from `failures` because it
    #: is not an error and must not be read as success either.
    unconfigured: int = 0

    @property
    def ok(self) -> bool:
        return not self.failures


@dataclass
class ReplayReport:
    """Every source replayed, and what went wrong where."""

    sources: list[SourceReplay] = field(default_factory=list)
    #: Source ids present in the ingest log with no adapter class to parse
    #: them. Named rather than counted: a source whose adapter was renamed
    #: or deleted leaves payloads that can never be re-derived, and a
    #: replay silently skipping them would report success while producing
    #: a smaller store than the one it claimed to reproduce.
    unclaimed: tuple[str, ...] = ()

    @property
    def facts(self) -> int:
        return sum(s.facts for s in self.sources)

    @property
    def entries(self) -> int:
        return sum(s.entries for s in self.sources)

    @property
    def unconfigured(self) -> tuple[SourceReplay, ...]:
        """Sources replayed without configuration their parse needed."""
        return tuple(s for s in self.sources if s.unconfigured)

    @property
    def ok(self) -> bool:
        return not self.unclaimed and all(s.ok for s in self.sources)

    @property
    def failures(self) -> tuple[tuple[str, int, str], ...]:
        return tuple((s.source, seq, message) for s in self.sources for seq, message in s.failures)


def needs_config(cls: type[SourceAdapter]) -> bool:
    """Whether this adapter's `parse` reads anything beyond its payload.

    Detected by the class overriding `parse_config`, not by calling it: a
    `parse_only` instance has no `__init__` state, so calling it would raise
    exactly where the question is being asked. Three of nineteen override
    it; the rest parse their bytes and nothing else.
    """
    return cls.parse_config is not SourceAdapter.parse_config


def logged_sources(log: IngestLog) -> tuple[str, ...]:
    """Source ids that actually appear in the log, in first-seen order."""
    seen: dict[str, None] = {}
    for entry in log.read():
        seen.setdefault(entry.source, None)
    return tuple(seen)


def replay_source(
    source_id: str,
    cls: type[SourceAdapter],
    payloads: PayloadStore,
    log: IngestLog,
    *,
    up_to_seq: int | None = None,
) -> Iterator[tuple[int, ParsedBatch | Exception]]:
    """Re-parse one source's log entries, yielding (seq, batch or error).

    Reimplements `SourceAdapter.replay` rather than calling it, because
    that one yields batches without their sequence numbers and a failure
    here needs to name the entry that caused it. The construction of
    `RawPayload` from the log entry is the same, and must stay so: the
    replayed provenance is only byte-identical to the original if the URI
    and fetch time come back exactly as recorded.
    """
    adapter = parse_only(cls, payloads, log)
    for entry in log.read(up_to_seq=up_to_seq):
        if entry.source != source_id:
            continue
        try:
            # Before parse, and per entry: two runs of one source can have
            # been configured differently, so the config belongs to the log
            # row rather than to the adapter.
            adapter.apply_parse_config(entry.parse_config or {})
            data = payloads.get(entry.payload_hash)
            payload = RawPayload(
                data=data,
                source_uri=entry.source_uri,
                fetched_at=entry.fetched_at,
            )
            yield entry.seq, adapter.parse(payload, entry.payload_hash)
        except Exception as exc:
            yield entry.seq, exc


def rebuild(
    store: FactWriter,
    payloads: PayloadStore,
    log: IngestLog,
    *,
    sources: tuple[str, ...] | None = None,
    up_to_seq: int | None = None,
    on_source: Callable[[str, int, int], None] | None = None,
    classes: dict[str, type[SourceAdapter]] | None = None,
) -> ReplayReport:
    """Re-derive every logged source into ``store``, without the network.

    Writes provenance before facts, batch by batch, so a large source is
    never held in memory whole — `frenchdata` alone re-derives 2.68 million
    facts from six payloads.

    ``store`` is a writer rather than a `DuckStore` so this cannot be
    pointed at the live store by accident: the caller has to have opened
    somewhere to write, and `treble replay` opens a new file.

    ``classes`` overrides discovery. Production passes nothing and gets the
    nineteen shipped adapters; the seam exists because a test adapter lives
    outside `treble.ingest` and is therefore invisible to `pkgutil`, and a
    rebuild that could only be exercised against the real package could
    only be tested on one machine's data.
    """
    classes = adapter_classes() if classes is None else classes
    logged = logged_sources(log)
    wanted = logged if sources is None else tuple(s for s in logged if s in sources)
    report = ReplayReport(unclaimed=tuple(s for s in wanted if s not in classes))

    for source_id in wanted:
        cls = classes.get(source_id)
        if cls is None:
            continue
        entries = facts = provenance = 0
        failures: list[tuple[int, str]] = []
        wants_config = needs_config(cls)
        unrecorded = {e.seq for e in log.read(up_to_seq=up_to_seq) if e.parse_config is None}
        unconfigured = 0
        for seq, result in replay_source(source_id, cls, payloads, log, up_to_seq=up_to_seq):
            entries += 1
            if wants_config and seq in unrecorded:
                unconfigured += 1
            if isinstance(result, Exception):
                failures.append((seq, f"{type(result).__name__}: {result}"))
                continue
            store.write_provenance(list(result.provenance))
            store.write_facts(list(result.facts))
            facts += len(result.facts)
            provenance += len(result.provenance)
        report.sources.append(
            SourceReplay(
                source=source_id,
                entries=entries,
                facts=facts,
                provenance=provenance,
                failures=tuple(failures),
                unconfigured=unconfigured,
            )
        )
        if on_source is not None:
            on_source(source_id, entries, facts)
    return report


__all__ = [
    "ReplayReport",
    "SourceReplay",
    "adapter_classes",
    "logged_sources",
    "needs_config",
    "parse_only",
    "rebuild",
    "replay_source",
]
