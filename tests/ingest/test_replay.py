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

import json
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
    needs_config,
    parse_only,
    rebuild,
    replay_source,
)
from treble.store.duck import DuckStore
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore

FETCHED = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

#: Adapters whose `parse` reads something beyond its payload, and therefore
#: record it in the ingest log so a replay can restore it.
#:
#: Both are filters — a CIK set and an ISIN set — that decide what comes out
#: of identical bytes. Neither is recoverable from the payload or the URI,
#: so recording is the only fix, and it only helps runs made after the
#: column existed. A pre-existing entry replays *unconfigured* and is
#: counted as such rather than reported as a success.
#:
#: `edgar-companyfacts` was the third case and is deliberately **not** here:
#: its acceptance times live in `edgar-submissions` payloads, which are
#: stored, so it derives them instead of recording them and replays
#: correctly even for entries written before any of this existed.
#:
#: The set should shrink. An adapter added with a fetch-dependent `parse`
#: lands here, and this is where that gets noticed.
NEEDS_RECORDED_CONFIG = {"edgar-bulk", "gleif-isin"}


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

    def test_exactly_the_declared_adapters_need_recorded_config(self) -> None:
        """Pinned in both directions. An adapter that starts needing config
        makes the store harder to reproduce and must be a deliberate,
        recorded decision; one that stops needing it should be removed from
        the set rather than left claiming a dependency it no longer has."""
        actual = {s for s, c in adapter_classes().items() if needs_config(c)}
        assert actual == NEEDS_RECORDED_CONFIG

    @pytest.mark.parametrize("source_id", sorted(NEEDS_RECORDED_CONFIG))
    def test_config_round_trips_through_json(self, source_id: str) -> None:
        """`parse_config` is stored as JSON, so anything it returns has to
        survive the trip. A set of ints that serialises and comes back as a
        list of strings would filter nothing and replay a superset."""
        cls = adapter_classes()[source_id]
        adapter = parse_only(cls, None, None)  # type: ignore[arg-type]
        adapter.apply_parse_config({"ciks": [320193, 789019], "isins": ["US0378331005"]})
        restored = json.loads(json.dumps(adapter.parse_config()))
        adapter.apply_parse_config(restored)
        assert adapter.parse_config() == restored, "config did not survive JSON"

    def test_companyfacts_derives_rather_than_records(self) -> None:
        """Its acceptance times come from another source's stored payloads,
        so it needs no recorded config and replays correctly even for the
        108 entries written before the column existed. Asserted because the
        alternative — adding it to `parse_config` — would have looked like a
        fix while leaving every existing entry degraded."""
        assert not needs_config(adapter_classes()["edgar-companyfacts"])

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


class TestRecordedConfig:
    """The log column that makes a filtering parse reproducible."""

    def test_config_written_at_ingest_comes_back_at_replay(self, tmp_path: Path) -> None:
        payloads = PayloadStore(tmp_path / "payloads")
        log = IngestLog(tmp_path / "ingest.db")
        log.append(
            source="s",
            payload_hash=payloads.put(b"x"),
            source_uri="https://x.invalid",
            fetched_at=FETCHED,
            parser_version="1",
            parse_config={"ciks": [1, 2]},
        )
        assert log.read()[0].parse_config == {"ciks": [1, 2]}

    def test_unrecorded_is_none_not_empty(self, tmp_path: Path) -> None:
        """`{}` says the adapter needed nothing; `None` says nobody asked.
        Collapsing them would make a degraded replay look like a clean one —
        the ambiguity that let this run unnoticed for a year."""
        payloads = PayloadStore(tmp_path / "payloads")
        log = IngestLog(tmp_path / "ingest.db")
        log.append(
            source="s",
            payload_hash=payloads.put(b"x"),
            source_uri="https://x.invalid",
            fetched_at=FETCHED,
            parser_version="1",
        )
        assert log.read()[0].parse_config is None

    def test_an_existing_log_gains_the_column(self, tmp_path: Path) -> None:
        """The migration runs on open. The live log had 488 rows written
        before this column existed and must keep reading."""
        db = tmp_path / "ingest.db"
        payloads = PayloadStore(tmp_path / "payloads")
        first = IngestLog(db)
        first.append(
            source="s",
            payload_hash=payloads.put(b"x"),
            source_uri="https://x.invalid",
            fetched_at=FETCHED,
            parser_version="1",
        )
        del first
        reopened = IngestLog(db)
        assert reopened.read()[0].parse_config is None

    def test_run_records_what_parse_config_returns(self, tmp_path: Path) -> None:
        """The seam that matters: `run` must write the config, or replay
        restores nothing and the whole mechanism is decorative."""
        payloads = PayloadStore(tmp_path / "payloads")
        log = IngestLog(tmp_path / "ingest.db")

        class _Configured(_Adapter):
            meta = SourceMeta(source_id="configured", description="f", licence="none")

            def parse_config(self) -> dict[str, object]:
                return {"kept": ["AAA"]}

        adapter = _Configured(payloads, log)
        adapter.fetch = lambda: iter(  # type: ignore[method-assign]
            [RawPayload(data=b"AAA", source_uri="https://x.invalid", fetched_at=FETCHED)]
        )
        list(adapter.run())
        assert log.read()[0].parse_config == {"kept": ["AAA"]}
