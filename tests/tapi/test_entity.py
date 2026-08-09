"""The entity graph, reachable from a service (spec §9.5).

core/entity_graph.py held the walks since WP7 and nothing called it --
partly because until the GLEIF relationship backfill there was no graph to
walk. There is now: 1,326,770 relationship facts across 373,125 LEI
subjects.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import TUID, SecurityQuery, YellowKey
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.entity import EntityUnknownError, ancestry_of, children_of
from treble.tapi.local import LocalTapi

KNOWN = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)
CHILD = TUID("lei:529900T8BM49AURSDO55")
DIRECT = "254900HROIFWPRGM1V77"
ULTIMATE = "5299005FF7ZR0O22AB19"


@pytest.fixture
def store(tmp_path: Path) -> DuckStore:
    return DuckStore(tmp_path / "t.db")


def _write(store: DuckStore, subject: TUID, edges: list[tuple[str, str]]) -> None:
    prov = Provenance(
        source_system="gleif-rr",
        source_uri="https://leidata.gleif.org/x",
        retrieved_at=KNOWN,
        method=ExtractionMethod.BULK_FILE,
        extractor_version="1",
        payload_hash="0" * 64,
    )
    store.write_provenance([prov])
    facts: list[Fact] = []
    for rel_type, target in edges:
        for field, value in (
            (f"gleif:rr:{rel_type}", target),
            (f"gleif:rr:{rel_type}:status", "ACTIVE"),
        ):
            facts.append(
                Fact(
                    subject=str(subject),
                    field=field,
                    value=value,
                    effective_from=date(2020, 1, 1),
                    effective_to=None,
                    knowledge_from=KNOWN,
                    provenance_id=prov.id,
                )
            )
    store.write_facts(facts)


class TestAncestry:
    def test_both_parents_are_read_as_gleif_stated_them(self, store: DuckStore) -> None:
        """Neither is derived from the other. GLEIF's ultimate-parent
        assertion may skip an intermediate holding company the direct edge
        names, and on the live store 3 of 6 sampled entities differ."""
        _write(
            store,
            CHILD,
            [
                ("IS_DIRECTLY_CONSOLIDATED_BY", DIRECT),
                ("IS_ULTIMATELY_CONSOLIDATED_BY", ULTIMATE),
            ],
        )
        found = ancestry_of(store, CHILD, as_of=LATER)
        assert str(found.direct_parent) == f"lei:{DIRECT}"
        assert str(found.ultimate_parent) == f"lei:{ULTIMATE}"
        assert found.parents_agree is False

    def test_agreement_is_reported_when_they_match(self, store: DuckStore) -> None:
        _write(
            store,
            CHILD,
            [
                ("IS_DIRECTLY_CONSOLIDATED_BY", DIRECT),
                ("IS_ULTIMATELY_CONSOLIDATED_BY", DIRECT),
            ],
        )
        assert ancestry_of(store, CHILD, as_of=LATER).parents_agree is True

    def test_the_edges_travel_for_the_drill_down(self, store: DuckStore) -> None:
        """SPTR shows the chain, not only its endpoints."""
        _write(store, CHILD, [("IS_DIRECTLY_CONSOLIDATED_BY", DIRECT)])
        assert len(ancestry_of(store, CHILD, as_of=LATER).edges) == 1

    def test_an_entity_with_no_relationship_facts_is_an_error(self, store: DuckStore) -> None:
        """An entity with no parent and an entity nobody filed for are
        different, and this is the second."""
        with pytest.raises(EntityUnknownError, match="no GLEIF relationship facts"):
            ancestry_of(store, TUID("lei:999900T8BM49AURSDO55"), as_of=LATER)

    def test_it_is_point_in_time(self, store: DuckStore) -> None:
        _write(store, CHILD, [("IS_DIRECTLY_CONSOLIDATED_BY", DIRECT)])
        with pytest.raises(EntityUnknownError):
            ancestry_of(store, CHILD, as_of=datetime(2026, 7, 1, tzinfo=UTC))


class TestDescent:
    def test_children_are_found_by_reverse_query(self, store: DuckStore) -> None:
        """A child asserts its parent on its own subject, so the subjects
        returned are the children and the value matched is the parent."""
        _write(store, CHILD, [("IS_DIRECTLY_CONSOLIDATED_BY", DIRECT)])
        assert children_of(store, TUID(f"lei:{DIRECT}"), as_of=LATER) == (CHILD,)

    def test_an_entity_with_no_children_returns_empty(self, store: DuckStore) -> None:
        """Empty rather than an error: an entity that owns nothing is a
        fact about the world, unlike one nobody has filed for."""
        _write(store, CHILD, [("IS_DIRECTLY_CONSOLIDATED_BY", DIRECT)])
        assert children_of(store, CHILD, as_of=LATER) == ()

    def test_it_is_point_in_time(self, store: DuckStore) -> None:
        _write(store, CHILD, [("IS_DIRECTLY_CONSOLIDATED_BY", DIRECT)])
        assert (
            children_of(store, TUID(f"lei:{DIRECT}"), as_of=datetime(2026, 7, 1, tzinfo=UTC)) == ()
        )

    def test_the_relationship_type_is_honoured(self, store: DuckStore) -> None:
        """A fund manager is not a consolidating parent, and a screen that
        conflated them would report an asset manager as owning its funds'
        balance sheets."""
        _write(store, CHILD, [("IS_FUND-MANAGED_BY", DIRECT)])
        assert children_of(store, TUID(f"lei:{DIRECT}"), as_of=LATER) == ()
        assert children_of(
            store, TUID(f"lei:{DIRECT}"), as_of=LATER, relationship_type="IS_FUND-MANAGED_BY"
        ) == (CHILD,)


class TestTheScreenBinding:
    """`sys:entity_owners` and `sys:entity_children` on LocalTapi. Until
    this, every service built in this stretch was reachable from another
    service and from nothing a user could open."""

    @staticmethod
    def _tapi(store: DuckStore) -> LocalTapi:
        return LocalTapi(store)

    def _instrument_with_lei(self, store: DuckStore) -> None:
        prov = Provenance(
            source_system="edgar-nport",
            source_uri="https://example.invalid/primary_doc.xml",
            retrieved_at=KNOWN,
            method=ExtractionMethod.DOCUMENT,
            extractor_version="1",
            payload_hash="1" * 64,
        )
        store.write_provenance([prov])
        store.write_facts(
            [
                Fact(
                    subject="cusip:037833100",
                    field="nport:lei",
                    value=str(CHILD).removeprefix("lei:"),
                    effective_from=date(2026, 6, 30),
                    effective_to=date(2026, 6, 30),
                    knowledge_from=KNOWN,
                    provenance_id=prov.id,
                )
            ]
        )

    def test_ownership_reaches_the_screen(self, store: DuckStore) -> None:
        self._instrument_with_lei(store)
        _write(
            store,
            CHILD,
            [
                ("IS_DIRECTLY_CONSOLIDATED_BY", DIRECT),
                ("IS_ULTIMATELY_CONSOLIDATED_BY", ULTIMATE),
            ],
        )
        rows = self._tapi(store).series(
            SecurityQuery(ticker="037833100", key=YellowKey.CORP, venue=None, descriptor=None),
            "sys:entity_owners",
            as_of=LATER,
        )
        flat = {r[0]: r[1] for r in rows}
        assert flat["DIRECT PARENT"] == f"lei:{DIRECT}"
        assert flat["ULTIMATE PARENT"] == f"lei:{ULTIMATE}"
        assert flat["PARENTS AGREE"].startswith("no")

    def test_an_instrument_with_no_lei_says_so(self, store: DuckStore) -> None:
        """An instrument with no filed issuer and one whose issuer is
        unknown render alike and are not alike."""
        prov = Provenance(
            source_system="edgar-nport",
            source_uri="https://example.invalid/x",
            retrieved_at=KNOWN,
            method=ExtractionMethod.DOCUMENT,
            extractor_version="1",
            payload_hash="2" * 64,
        )
        store.write_provenance([prov])
        store.write_facts(
            [
                Fact(
                    subject="cusip:037833100",
                    field="nport:title",
                    value="Some Bond",
                    effective_from=date(2026, 6, 30),
                    effective_to=date(2026, 6, 30),
                    knowledge_from=KNOWN,
                    provenance_id=prov.id,
                )
            ]
        )
        rows = self._tapi(store).series(
            SecurityQuery(ticker="037833100", key=YellowKey.CORP, venue=None, descriptor=None),
            "sys:entity_owners",
            as_of=LATER,
        )
        assert "carries no LEI" in str(rows[0][0])
