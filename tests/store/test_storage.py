"""The storage budget, and specifically that it can fail.

A gate is worth what its failure path is worth. `verdict` is pure so that
path can be driven from constructed reports rather than from whatever is
on the developer's disk — every threshold below is exercised in both
directions, because a check only ever observed to pass is the exact defect
this repository keeps finding in its own work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from treble.store.duck import DuckStore
from treble.store.storage import (
    DEFAULT_WASTE_LIMIT,
    HOT_ROW_LIMIT,
    WASTE_LIMIT_ENV,
    Component,
    StorageReport,
    free_list_bytes,
    maintenance_due,
    measure,
    verdict,
    waste_limit,
)

MB = 1024 * 1024


def _report(*components: Component, root: Path = Path("/data")) -> StorageReport:
    return StorageReport(root=root, components=components)


def _waste(name: str, size: int) -> Component:
    return Component(name=name, path=Path("/data") / name, size=size, waste=size, remedy="delete")


class TestVerdict:
    def test_a_clean_store_passes(self) -> None:
        report = _report(Component(name="payloads", path=Path("/data/payloads"), size=605 * MB))
        assert verdict(report, limit=256 * MB).ok

    def test_waste_over_the_limit_fails(self) -> None:
        """The failure path. Without a test that lands here, everything
        else in this file only proves the check is silent."""
        result = verdict(_report(_waste("treble.db.bak-1", 336 * MB)), limit=256 * MB)
        assert not result.ok
        assert result.reasons

    def test_waste_under_the_limit_passes(self) -> None:
        assert verdict(_report(_waste("treble.db.bak-1", 100 * MB)), limit=256 * MB).ok

    def test_the_limit_is_a_ceiling_not_a_floor(self) -> None:
        """Exactly at the limit passes; one byte over fails. Pinned
        because an off-by-one here is invisible in normal operation and
        decides whether the gate ever fires."""
        assert verdict(_report(_waste("b", 256 * MB)), limit=256 * MB).ok
        assert not verdict(_report(_waste("b", 256 * MB + 1)), limit=256 * MB).ok

    def test_waste_accumulates_across_components(self) -> None:
        """The incident was two files, each under any sane single-file
        threshold, that together were 672 MB."""
        report = _report(_waste("bak-1", 200 * MB), _waste("bak-2", 200 * MB))
        assert not verdict(report, limit=256 * MB).ok

    def test_every_wasteful_component_is_named_in_the_reasons(self) -> None:
        report = _report(_waste("bak-1", 200 * MB), _waste("bak-2", 200 * MB))
        reasons = " ".join(verdict(report, limit=256 * MB).reasons)
        assert "bak-1" in reasons and "bak-2" in reasons

    def test_a_partial_compaction_fails_however_small(self) -> None:
        """Size cannot catch this one — a truncated Parquet file is
        typically bytes — and it means a compaction died partway."""
        partial = Component(
            name="lei.parquet.compacting",
            path=Path("/data/cold/lei.parquet.compacting"),
            size=12,
            waste=12,
            remedy="delete it and re-run `treble compact`",
        )
        result = verdict(_report(partial), limit=DEFAULT_WASTE_LIMIT)
        assert not result.ok
        assert "did not finish" in " ".join(result.reasons)


class TestComponentInvariants:
    def test_waste_cannot_exceed_size(self) -> None:
        with pytest.raises(ValueError, match="exceeds size"):
            Component(name="x", path=Path("/x"), size=10, waste=11, remedy="r")

    def test_waste_without_a_remedy_is_refused(self) -> None:
        """A report that says "336 MB is reclaimable" and cannot say by
        what command is a report that gets ignored."""
        with pytest.raises(ValueError, match="no remedy"):
            Component(name="x", path=Path("/x"), size=10, waste=10)


class TestWasteLimit:
    def test_the_default_applies_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(WASTE_LIMIT_ENV, raising=False)
        assert waste_limit() == DEFAULT_WASTE_LIMIT

    def test_the_environment_overrides_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(WASTE_LIMIT_ENV, str(64 * MB))
        assert waste_limit() == 64 * MB

    def test_it_is_read_at_call_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bound at import, this would be unpatchable and the operator's
        setting would depend on import order — the `MANDATE_DIR` mistake."""
        monkeypatch.setenv(WASTE_LIMIT_ENV, str(1 * MB))
        first = waste_limit()
        monkeypatch.setenv(WASTE_LIMIT_ENV, str(2 * MB))
        assert (first, waste_limit()) == (1 * MB, 2 * MB)

    def test_an_unparseable_value_is_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Not a silent fallback: `512MB` meaning "the default" would make
        the gate pass for a reason nobody chose."""
        monkeypatch.setenv(WASTE_LIMIT_ENV, "512MB")
        with pytest.raises(ValueError, match="not an integer"):
            waste_limit()

    def test_a_negative_value_is_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(WASTE_LIMIT_ENV, "-1")
        with pytest.raises(ValueError, match="must not be negative"):
            waste_limit()


class TestMeasure:
    def test_a_missing_directory_measures_as_empty(self, tmp_path: Path) -> None:
        """The CI path. It must not raise, and it must be distinguishable
        from a directory that exists and is clean."""
        report = measure(tmp_path / "absent")
        assert report.components == () and report.size == 0

    def test_hand_made_backups_are_waste(self, tmp_path: Path) -> None:
        (tmp_path / "treble.db.bak-20260825-090507").write_bytes(b"x" * 2048)
        (component,) = measure(tmp_path).components
        assert component.waste == 2048
        assert component.remedy is not None

    @pytest.mark.parametrize(
        "name",
        [
            "treble.db.bak",
            "treble.db.bak-20260825-090507",
            "treble.db.backup",
            "store.old",
            "treble.db.orig",
            "treble.db.copy",
        ],
    )
    def test_the_shapes_people_actually_write(self, tmp_path: Path, name: str) -> None:
        """The timestamped form is what both files in the incident were
        named. A check matching only the bare suffix would have caught
        neither."""
        (tmp_path / name).write_bytes(b"x" * 512)
        assert measure(tmp_path).waste == 512

    def test_payloads_are_never_waste(self, tmp_path: Path) -> None:
        """The one directory that must never be suggested for deletion:
        content-addressed source bytes, from which the derived store could
        be rebuilt, and which no source will serve again once it moves on."""
        payloads = tmp_path / "payloads" / "ab" / "cd"
        payloads.mkdir(parents=True)
        (payloads / "abcd.gz").write_bytes(b"x" * 4096)
        (component,) = measure(tmp_path).components
        assert (component.name, component.waste, component.remedy) == ("payloads", 0, None)

    def test_a_directory_is_measured_recursively(self, tmp_path: Path) -> None:
        nested = tmp_path / "cold" / "deep"
        nested.mkdir(parents=True)
        (nested / "a.parquet").write_bytes(b"x" * 100)
        (tmp_path / "cold" / "b.parquet").write_bytes(b"x" * 50)
        assert measure(tmp_path).size == 150

    def test_a_partial_compaction_file_is_waste(self, tmp_path: Path) -> None:
        (tmp_path / "lei.parquet.compacting").write_bytes(b"x" * 64)
        (component,) = measure(tmp_path).components
        assert component.waste == 64
        assert not verdict(measure(tmp_path)).ok

    def test_every_entry_is_accounted_for(self, tmp_path: Path) -> None:
        """The report answers "what is using the space", so a component
        with no waste still has to appear."""
        (tmp_path / "payloads").mkdir()
        (tmp_path / "ingest.db").write_bytes(b"x" * 10)
        (tmp_path / "company_index.json").write_bytes(b"x" * 10)
        assert {c.name for c in measure(tmp_path).components} == {
            "payloads",
            "ingest.db",
            "company_index.json",
        }


class TestMaintenanceDue:
    """The condition that makes an ingest clean up after itself."""

    def test_a_grown_hot_tier_is_due(self) -> None:
        assert maintenance_due(1_953_485, limit=HOT_ROW_LIMIT)

    def test_a_compacted_store_is_not_due(self) -> None:
        """The important half. If this were true on a clean store, every
        refresh would pay a full Parquet rewrite for nothing and the flag
        to skip it would become the default."""
        assert not maintenance_due(0, limit=HOT_ROW_LIMIT)

    def test_the_limit_is_exclusive(self) -> None:
        assert not maintenance_due(1000, limit=1000)
        assert maintenance_due(1001, limit=1000)

    def test_the_default_would_have_fired_before_the_incident(self) -> None:
        """The hot tier was 1,953,485 rows in a 336 MB file. A threshold
        set above that would have watched the whole thing happen."""
        assert maintenance_due(1_953_485)
        assert HOT_ROW_LIMIT < 1_953_485


class TestHotFactCount:
    def test_it_counts_the_hot_tier_not_both(self, tmp_path: Path) -> None:
        """`fact_count` spans both tiers. Using it for the maintenance
        decision would compact a freshly compacted store on every ingest,
        because the cold tier is most of the count and none of the work."""
        from datetime import UTC, date, datetime

        from treble.core.facts import Fact
        from treble.core.identifiers import TUID
        from treble.core.provenance import ExtractionMethod, Provenance

        store = DuckStore(tmp_path / "h.db")
        record = Provenance(
            source_system="t",
            source_uri="https://example.invalid/x",
            retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
            method=ExtractionMethod.BULK_FILE,
            extractor_version="1",
            payload_hash="a" * 64,
        )
        store.write_provenance([record])
        store.write_facts(
            [
                Fact(
                    subject=TUID("lei:X"),
                    field="f",
                    value=1.0,
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    knowledge_from=datetime(2026, 1, 1, tzinfo=UTC),
                    provenance_id=record.id,
                )
            ]
        )
        assert store.hot_fact_count() == 1

        store.compact(before=datetime(2026, 6, 1, tzinfo=UTC))
        assert store.hot_fact_count() == 0, "compaction emptied the hot tier"
        assert store.fact_count() == 1, "and the fact is still readable"


class TestFreeList:
    def test_a_missing_database_has_no_free_list(self, tmp_path: Path) -> None:
        assert free_list_bytes(tmp_path / "absent.db") == 0

    def test_a_real_database_reports_a_free_list(self, tmp_path: Path) -> None:
        """Not asserting a specific number — DuckDB's allocation is its
        own business. Asserting the call works against a real file and
        returns something a budget can be compared against."""
        db = tmp_path / "treble.db"
        DuckStore(db)
        assert free_list_bytes(db) >= 0

    def test_a_file_that_is_not_a_database_is_not_an_error(self, tmp_path: Path) -> None:
        """Measuring must never be the thing that breaks the gate."""
        junk = tmp_path / "treble.db"
        junk.write_bytes(b"not a database")
        assert free_list_bytes(junk) == 0

    def test_the_free_list_never_exceeds_the_file(self, tmp_path: Path) -> None:
        """`measure` clamps it. A `waste` larger than `size` would trip
        the `Component` invariant and turn a measurement into a crash."""
        db = tmp_path / "treble.db"
        DuckStore(db)
        (component,) = [c for c in measure(tmp_path).components if c.name == "treble.db"]
        assert component.waste <= component.size
