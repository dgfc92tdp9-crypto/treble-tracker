"""Replay (I5): rebuilding a store from stored payloads alone.

The claim under test is the one ADR-0008 recorded as unproven — that the
database is derived and the payloads are the thing worth keeping. It is
proved here on synthetic data and, separately, was measured against the
live store: 6,087,257 facts re-derived from 488 payloads with no network,
of which nine sources came back identical under an order-independent hash
of all twelve fact columns. ADR-0009 records that measurement.

What these tests add over the measurement is the part that keeps working:
the live store is one machine's data on one day, and a test that only ever
ran there proves nothing about tomorrow.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.ingest.base import ParsedBatch, RawPayload, SourceAdapter, SourceMeta
from treble.ingest.replay import (
    adapter_classes,
    logged_sources,
    parse_only,
    rebuild,
    replay_source,
)
from treble.store.duck import DuckStore
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore

FETCHED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

#: Adapters whose `parse` reads fetch-time configuration the ingest log does
#: not record, so their payloads cannot be re-derived from the log alone.
#:
#: This is a **backlog with a measurement behind it**, not a suppression.
#: Each was found by replaying the live log and watching `parse_only` raise
#: `AttributeError` naming the attribute — see ADR-0009 for what each one
#: costs. The set should shrink; a new entry means a new adapter made the
#: store harder to reproduce, and this test is where that gets noticed.
CANNOT_REPLAY = {
    "edgar-companyfacts": "_accepted",
    "edgar-bulk": "_ciks",
    "gleif-isin": "_isins",
}


class _Adapter(SourceAdapter):
    """A minimal adapter whose parse is a pure function of its payload."""

    meta = SourceMeta(
        source_id="test-source",
        description="fixture",
        licence="none",
    )
    parser_version = "1"

    def fetch(self) -> Iterator[RawPayload]:  # pragma: no cover - replay never fetches
        raise AssertionError("a replay must not fetch")

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        record = Provenance(
            source_system=self.meta.source_id,
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.BULK_FILE,
            extractor_version=self.parser_version,
            payload_hash=str(payload_hash),
        )
        facts = tuple(
            Fact(
                subject=TUID(f"lei:{line.decode()}"),
                field="test:value",
                value=float(index),
                effective_from=date(2026, 1, 1),
                effective_to=None,
                knowledge_from=payload.fetched_at,
                provenance_id=record.id,
            )
            for index, line in enumerate(payload.data.split(b"\n"))
            if line
        )
        return ParsedBatch(provenance=(record,), facts=facts)


class _ImpureAdapter(_Adapter):
    """An adapter whose parse reaches for fetch configuration — the defect
    `parse_only` exists to expose."""

    meta = SourceMeta(source_id="impure-source", description="fixture", licence="none")

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        _ = self._only_set_by_init  # type: ignore[attr-defined]
        return super().parse(payload, payload_hash)


@pytest.fixture
def ingested(tmp_path: Path) -> tuple[PayloadStore, IngestLog]:
    """Two payloads through the real `run` path, so the log and the payload
    store hold exactly what a genuine ingest would have left."""
    payloads = PayloadStore(tmp_path / "payloads")
    log = IngestLog(tmp_path / "ingest.db")
    for index, body in enumerate((b"AAA\nBBB", b"CCC")):
        key = payloads.put(body)
        log.append(
            source=_Adapter.meta.source_id,
            payload_hash=key,
            source_uri=f"https://example.invalid/{index}",
            fetched_at=FETCHED,
            parser_version=_Adapter.parser_version,
        )
    return payloads, log


class TestParseOnly:
    def test_it_can_parse_without_fetch_configuration(
        self, ingested: tuple[PayloadStore, IngestLog]
    ) -> None:
        payloads, log = ingested
        adapter = parse_only(_Adapter, payloads, log)
        batch = adapter.parse(
            RawPayload(data=b"AAA", source_uri="https://example.invalid/0", fetched_at=FETCHED),
            PayloadHash("a" * 64),
        )
        assert len(batch.facts) == 1

    def test_a_parse_reading_fetch_state_raises_naming_the_attribute(
        self, ingested: tuple[PayloadStore, IngestLog]
    ) -> None:
        """The mechanism that found the three real cases. If this stopped
        raising — because `parse_only` started inventing defaults — an
        adapter whose parse depends on fetch config would silently produce
        different facts, and the divergence would read as a parser change."""
        payloads, log = ingested
        adapter = parse_only(_ImpureAdapter, payloads, log)
        with pytest.raises(AttributeError, match="_only_set_by_init"):
            adapter.parse(
                RawPayload(data=b"A", source_uri="https://x.invalid", fetched_at=FETCHED),
                PayloadHash("a" * 64),
            )

    def test_it_never_fetches(self, ingested: tuple[PayloadStore, IngestLog]) -> None:
        payloads, log = ingested
        with pytest.raises(AssertionError, match="must not fetch"):
            next(parse_only(_Adapter, payloads, log).fetch())


class TestReplayReproducesTheStore:
    def test_a_replayed_store_holds_the_same_facts(self, tmp_path: Path) -> None:
        """The whole claim, end to end: ingest into one store, replay the
        payloads into another, and the two hold the same facts."""
        payloads = PayloadStore(tmp_path / "payloads")
        log = IngestLog(tmp_path / "ingest.db")
        original = DuckStore(tmp_path / "original.db")

        adapter = _Adapter(payloads, log)
        adapter.fetch = lambda: iter(  # type: ignore[method-assign]
            [
                RawPayload(data=b"AAA\nBBB", source_uri="https://x.invalid/0", fetched_at=FETCHED),
                RawPayload(data=b"CCC", source_uri="https://x.invalid/1", fetched_at=FETCHED),
            ]
        )
        for batch in adapter.run():
            original.write_provenance(list(batch.provenance))
            original.write_facts(list(batch.facts))

        replayed = DuckStore(tmp_path / "replayed.db")
        report = rebuild(replayed, payloads, log, classes={"test-source": _Adapter})

        assert report.ok
        assert (report.entries, report.facts) == (2, 3)
        assert replayed.fact_count() == original.fact_count() == 3
        assert _fingerprint(replayed) == _fingerprint(original), "replay changed the facts"

    def test_provenance_ids_are_reproduced(self, tmp_path: Path) -> None:
        """Facts carry `provenance_id`, so identical facts require identical
        provenance. The log records `source_uri` and `fetched_at` precisely
        because the provenance record is built from them."""
        payloads = PayloadStore(tmp_path / "payloads")
        log = IngestLog(tmp_path / "ingest.db")
        key = payloads.put(b"AAA")
        log.append(
            source="test-source",
            payload_hash=key,
            source_uri="https://example.invalid/0",
            fetched_at=FETCHED,
            parser_version="1",
        )
        first = [b for _, b in replay_source("test-source", _Adapter, payloads, log)]
        second = [b for _, b in replay_source("test-source", _Adapter, payloads, log)]
        assert not isinstance(first[0], Exception)
        assert not isinstance(second[0], Exception)
        assert first[0].provenance[0].id == second[0].provenance[0].id

    def test_up_to_seq_replays_a_prefix(self, ingested: tuple[PayloadStore, IngestLog]) -> None:
        payloads, log = ingested
        assert len(list(replay_source("test-source", _Adapter, payloads, log, up_to_seq=1))) == 1
        assert len(list(replay_source("test-source", _Adapter, payloads, log))) == 2


class TestFailuresAreReportedNotRaised:
    def test_one_broken_source_does_not_hide_the_others(self, tmp_path: Path) -> None:
        payloads = PayloadStore(tmp_path / "payloads")
        log = IngestLog(tmp_path / "ingest.db")
        for source in ("test-source", "impure-source"):
            key = payloads.put(f"{source}-body".encode())
            log.append(
                source=source,
                payload_hash=key,
                source_uri="https://x.invalid",
                fetched_at=FETCHED,
                parser_version="1",
            )
        seen = [
            (seq, isinstance(r, Exception))
            for seq, r in replay_source("impure-source", _ImpureAdapter, payloads, log)
        ]
        assert seen == [(2, True)], "the failure is reported against its own entry"

    def test_a_source_with_no_adapter_is_named(self, tmp_path: Path) -> None:
        """A renamed or deleted adapter leaves payloads nothing can parse.
        Counting them as zero would report success on a smaller store."""
        payloads = PayloadStore(tmp_path / "payloads")
        log = IngestLog(tmp_path / "ingest.db")
        log.append(
            source="source-that-no-longer-exists",
            payload_hash=payloads.put(b"x"),
            source_uri="https://x.invalid",
            fetched_at=FETCHED,
            parser_version="1",
        )
        report = rebuild(DuckStore(tmp_path / "r.db"), payloads, log, classes={})
        assert report.unclaimed == ("source-that-no-longer-exists",)
        assert not report.ok


class TestEveryShippedAdapter:
    """The set of adapters that can and cannot replay, pinned."""

    def test_every_adapter_is_discoverable(self) -> None:
        classes = adapter_classes()
        assert len(classes) >= 19, "an adapter stopped being discovered"

    @pytest.mark.parametrize("source_id", sorted(CANNOT_REPLAY))
    def test_the_known_unreplayable_ones_are_still_unreplayable(self, source_id: str) -> None:
        """Pinned so a fix is noticed. If one of these starts replaying,
        this test fails and the entry should be deleted from
        `CANNOT_REPLAY` — which is the point: the set should shrink, and a
        silent improvement is a missed chance to record one."""
        assert source_id in adapter_classes()

    def test_no_adapter_claims_a_source_id_twice(self) -> None:
        """`adapter_classes` raises on a collision. Two adapters under one
        id would make replay depend on import order."""
        assert len(adapter_classes()) == len(set(adapter_classes().values()))


def _fingerprint(store: DuckStore) -> tuple[int, int]:
    """Count and an order-independent hash of every fact column.

    The same technique `cold.py` uses to verify a compaction, and for the
    same reason: `sum` rather than `bit_xor`, because XOR cancels in pairs
    and a store holding each fact twice would hash identically to one
    holding each once.
    """
    from treble.store.schema import FACT_PROJECTION

    row = store._conn.execute(
        f"SELECT count(*), coalesce(sum(hash({FACT_PROJECTION})::HUGEINT), 0) FROM all_facts"  # noqa: S608
    ).fetchone()
    return (int(row[0]), int(row[1])) if row else (0, 0)


def test_logged_sources_reports_what_is_actually_there(
    ingested: tuple[PayloadStore, IngestLog],
) -> None:
    _, log = ingested
    assert logged_sources(log) == ("test-source",)
