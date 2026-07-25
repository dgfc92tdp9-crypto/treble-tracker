"""I2 kill-tests, query layer: as_of is required; no result leaks future knowledge;
restatements append and never rewrite history."""

import inspect
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from treble.core.facts import Fact
from treble.core.identifiers import new_tuid
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store import protocols
from treble.store.duck import DuckStore

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def make_provenance(version: str) -> Provenance:
    return Provenance(
        source_system="test",
        source_uri=f"https://example.test/filing/{version}",
        retrieved_at=BASE,
        method=ExtractionMethod.XBRL,
        extractor_version=version,
    )


class TestProtocolShape:
    """ADR-0001: append-only is a property of the interface."""

    @pytest.mark.parametrize(
        "proto", [protocols.Store, protocols.HistoryStore, protocols.IngestLogP]
    )
    def test_no_update_or_delete_members(self, proto: type) -> None:
        members = [name for name, _ in inspect.getmembers(proto) if not name.startswith("_")]
        mutating = ("update", "delete", "remove", "drop")
        forbidden = [m for m in members if any(w in m.lower() for w in mutating)]
        assert forbidden == []

    def test_reads_require_as_of(self) -> None:
        for method_name in ("read",):
            sig = inspect.signature(getattr(protocols.Store, method_name))
            param = sig.parameters["as_of"]
            assert param.kind == inspect.Parameter.KEYWORD_ONLY
            assert param.default is inspect.Parameter.empty

    def test_duckstore_rejects_naive_as_of(self, tmp_path: Path) -> None:
        store = DuckStore(tmp_path / "t.db")
        with pytest.raises(ValueError, match="timezone-aware"):
            store.read(new_tuid(), "PX_LAST", as_of=datetime(2026, 1, 1))  # noqa: DTZ001


class TestRestatement:
    def test_restatement_appends_and_point_in_time_reads_see_original(self, tmp_path: Path) -> None:
        store = DuckStore(tmp_path / "t.db")
        subject = new_tuid()
        original_prov, restated_prov = make_provenance("orig"), make_provenance("restated")
        store.write_provenance([original_prov, restated_prov])

        filed = BASE
        restated = BASE + timedelta(days=90)
        period = date(2025, 12, 31)
        store.write_facts(
            [
                Fact(
                    subject=subject,
                    field="REVENUE",
                    value=100.0,
                    effective_from=period,
                    effective_to=period,
                    knowledge_from=filed,
                    provenance_id=original_prov.id,
                ),
                Fact(
                    subject=subject,
                    field="REVENUE",
                    value=95.0,
                    effective_from=period,
                    effective_to=period,
                    knowledge_from=restated,
                    provenance_id=restated_prov.id,
                ),
            ]
        )

        # Before the restatement was knowable: the originally filed value.
        [before] = store.read(subject, "REVENUE", as_of=filed + timedelta(days=1))
        assert before.value == 100.0
        # After: latest knowledge wins.
        [after] = store.read(subject, "REVENUE", as_of=restated + timedelta(days=1))
        assert after.value == 95.0
        # Before anything was filed: the world knew nothing.
        assert store.read(subject, "REVENUE", as_of=filed - timedelta(days=1)) == []


@settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    knowledge_offsets=st.lists(
        st.integers(min_value=0, max_value=365), min_size=1, max_size=6, unique=True
    ),
    as_of_offset=st.integers(min_value=-30, max_value=400),
)
def test_no_query_returns_knowledge_from_after_as_of(
    tmp_path: Path, knowledge_offsets: list[int], as_of_offset: int
) -> None:
    """The I2 property named in CLAUDE.md §1, asserted over generated histories."""
    store = DuckStore(tmp_path / f"prop-{abs(hash(tuple(knowledge_offsets)))}-{as_of_offset}.db")
    prov = make_provenance("prop")
    store.write_provenance([prov])
    subject = new_tuid()
    store.write_facts(
        [
            Fact(
                subject=subject,
                field="PX_LAST",
                value=float(i),
                effective_from=date(2026, 1, 1),
                knowledge_from=BASE + timedelta(days=offset),
                provenance_id=prov.id,
            )
            for i, offset in enumerate(knowledge_offsets)
        ]
    )
    as_of = BASE + timedelta(days=as_of_offset)
    results = store.read(subject, "PX_LAST", as_of=as_of)
    assert all(f.knowledge_from <= as_of for f in results)
    # Latest-knowledge-wins: at most one row per effective period.
    assert len(results) <= 1
    visible = [o for o in knowledge_offsets if BASE + timedelta(days=o) <= as_of]
    if visible:
        assert results[0].value == float(knowledge_offsets.index(max(visible)))
    else:
        assert results == []
