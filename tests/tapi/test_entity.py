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

from treble.core.entity_graph import relationship_status_field
from treble.core.facts import Fact
from treble.core.identifiers import TUID, SecurityQuery, YellowKey
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.entity import (
    EntityUnknownError,
    ParentOutcome,
    ancestry_of,
    children_of,
)
from treble.tapi.local import LocalTapi

KNOWN = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)
CHILD = TUID("lei:529900T8BM49AURSDO55")
DIRECT = "254900HROIFWPRGM1V77"
ULTIMATE = "5299005FF7ZR0O22AB19"


@pytest.fixture
def store(tmp_path: Path) -> DuckStore:
    return DuckStore(tmp_path / "t.db")


def _write(store: DuckStore, subject: TUID, edges: list[tuple[str, ...]]) -> None:
    """Write relationship records for `subject`.

    Each edge is ``(relationship_type, counterparty)``, optionally with a
    third element giving the RelationshipStatus (default ACTIVE).
    """
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
    for edge in edges:
        rel_type, target = edge[0], edge[1]
        status = edge[2] if len(edge) > 2 else "ACTIVE"
        facts.append(
            Fact(
                subject=str(subject),
                field=relationship_status_field(rel_type, target),
                value=status,
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

    def test_a_child_whose_record_has_lapsed_is_not_in_the_family(self, store: DuckStore) -> None:
        """Descent used to match the counterparty alone, so it returned
        every entity that had *ever* named this parent. A family listing
        former subsidiaries alongside current ones, indistinguishable, is
        the same defect as naming a lapsed parent — read the other way."""
        _write(store, CHILD, [("IS_DIRECTLY_CONSOLIDATED_BY", DIRECT, "INACTIVE")])
        assert children_of(store, TUID(f"lei:{DIRECT}"), as_of=LATER) == ()


class TestTwoRecordsForOneEntity:
    """Both records must survive the store's visibility window.

    They share subject, relationship type, effective period, knowledge time
    and provenance — one bulk file — so under the old two-fact encoding
    they landed in one partition and `row_number() ... WHERE rn = 1`
    showed one counterparty and one status, chosen independently.
    """

    ANNULLED = "894500NGP61K2MQO3X40"
    ACTIVE = "969500WDCPJAW65OHW35"

    def _write_both(self, store: DuckStore) -> None:
        _write(
            store,
            CHILD,
            [
                ("IS_ULTIMATELY_CONSOLIDATED_BY", self.ANNULLED, "NULL"),
                ("IS_ULTIMATELY_CONSOLIDATED_BY", self.ACTIVE, "ACTIVE"),
            ],
        )

    def test_both_records_are_visible_through_the_window(self, store: DuckStore) -> None:
        self._write_both(store)
        edges = ancestry_of(store, CHILD, as_of=LATER).edges
        assert {str(e.parent) for e in edges} == {
            f"lei:{self.ANNULLED}",
            f"lei:{self.ACTIVE}",
        }

    def test_each_keeps_the_status_it_was_filed_with(self, store: DuckStore) -> None:
        self._write_both(store)
        edges = ancestry_of(store, CHILD, as_of=LATER).edges
        assert {str(e.parent): e.status for e in edges} == {
            f"lei:{self.ANNULLED}": "NULL",
            f"lei:{self.ACTIVE}": "ACTIVE",
        }

    def test_the_active_one_is_reported_as_the_parent(self, store: DuckStore) -> None:
        # The annulled counterparty sorts first, so this fails for any
        # resolver that orders by LEI, arrival or provenance.
        self._write_both(store)
        found = ancestry_of(store, CHILD, as_of=LATER)
        assert str(found.ultimate_parent) == f"lei:{self.ACTIVE}"

    def test_only_the_active_record_makes_a_family(self, store: DuckStore) -> None:
        self._write_both(store)
        assert children_of(
            store,
            TUID(f"lei:{self.ACTIVE}"),
            as_of=LATER,
            relationship_type="IS_ULTIMATELY_CONSOLIDATED_BY",
        ) == (CHILD,)
        assert (
            children_of(
                store,
                TUID(f"lei:{self.ANNULLED}"),
                as_of=LATER,
                relationship_type="IS_ULTIMATELY_CONSOLIDATED_BY",
            )
            == ()
        )

    def test_when_neither_is_active_no_parent_is_named(self, store: DuckStore) -> None:
        _write(
            store,
            CHILD,
            [
                ("IS_ULTIMATELY_CONSOLIDATED_BY", self.ANNULLED, "NULL"),
                ("IS_ULTIMATELY_CONSOLIDATED_BY", self.ACTIVE, "INACTIVE"),
            ],
        )
        found = ancestry_of(store, CHILD, as_of=LATER)
        assert found.ultimate_parent is None
        assert found.ultimate.outcome is ParentOutcome.NONE_ACTIVE
        assert len(found.ultimate.candidates) == 2


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


class TestTwoFilingsAgainstOneCounterparty:
    """GLEIF files a live record and a withdrawn one on the same day.

    Same child, same type, same counterparty, same relationship period, so
    they share a partition and the ordinary visibility window returns one
    of them. On the live store there are three, each an ACTIVE/PUBLISHED
    beside a NULL/ANNULLED or NULL/PENDING_ARCHIVAL record:

        lei:549300DNIBWLTWVNIK28 -> 5299005SPZ1QYL51JD25
        lei:894500UD73S4NGTZLD55 -> 5299003O1XGBX95Y6O90
        lei:300300878WUHIG688A71 -> 300300827ML7YLMPAZ49

    Both rows name the *same* counterparty, so the parent was never
    genuinely in doubt — but the store was only surfacing the live record
    because `'ACTIVE'` sorts before `'NULL'`, which is alphabet rather
    than evidence.
    """

    CHILD = TUID("lei:549300DNIBWLTWVNIK28")
    PARENT = "5299005SPZ1QYL51JD25"

    def _write_pair(self, store: DuckStore, live: str, withdrawn: str) -> None:
        _write(
            store,
            self.CHILD,
            [
                ("IS_ULTIMATELY_CONSOLIDATED_BY", self.PARENT, live),
                ("IS_ULTIMATELY_CONSOLIDATED_BY", self.PARENT, withdrawn),
            ],
        )

    def test_both_filings_reach_the_resolver(self, store: DuckStore) -> None:
        # The evidence must arrive intact; everything below depends on it.
        self._write_pair(store, "ACTIVE", "NULL")
        edges = ancestry_of(store, self.CHILD, as_of=LATER).edges
        assert sorted(e.status or "" for e in edges) == ["ACTIVE", "NULL"]

    def test_the_parent_resolves_from_the_active_filing(self, store: DuckStore) -> None:
        self._write_pair(store, "ACTIVE", "NULL")
        found = ancestry_of(store, self.CHILD, as_of=LATER)
        assert str(found.ultimate_parent) == f"lei:{self.PARENT}"
        assert found.ultimate.outcome is ParentOutcome.RESOLVED

    def test_it_does_not_depend_on_the_live_status_sorting_first(self, store: DuckStore) -> None:
        """The point of the whole exercise.

        `'ACTIVE'` precedes `'NULL'` alphabetically, so a resolver riding
        on `value_text` ordering looks correct on the real data. Here the
        superseded record is `'ABANDONED'` — earlier in the alphabet than
        `'ACTIVE'` — so string order now points at the *dead* filing and
        only reading the status can still get this right.
        """
        self._write_pair(store, "ACTIVE", "ABANDONED")
        found = ancestry_of(store, self.CHILD, as_of=LATER)
        assert str(found.ultimate_parent) == f"lei:{self.PARENT}"

    def test_two_withdrawn_filings_still_refuse(self, store: DuckStore) -> None:
        # Seeing both records must not be mistaken for endorsing either.
        self._write_pair(store, "INACTIVE", "NULL")
        found = ancestry_of(store, self.CHILD, as_of=LATER)
        assert found.ultimate_parent is None
        assert found.ultimate.outcome is ParentOutcome.NONE_ACTIVE
