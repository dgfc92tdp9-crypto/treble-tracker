"""`RELS` — related securities by legal ownership.

Two relations that must not blur into one: the identical legal entity, and
a corporate family sharing an ultimate parent. Bayer US Finance and Bayer
US Finance II are separate LEIs, file separately, and are one credit to
anyone trading them — but they are not the same issuer, and a curve fitted
across both would be a different claim from one fitted across either.

The number this screen exists to avoid getting wrong is the pair: on the
live store a Bayer entity sits under a parent with 130 entities of which
one has a bond here. Publishing one figure without the other turns a vast
group into a pair, or a pair into 130 tradeable lines.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.entity_graph import relationship_state_field, relationship_state_value
from treble.core.facts import Fact
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.related import NoRelationsError, related_securities

KNOWN = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
DAY = date(2026, 3, 31)
PARENT = "549300J4U55H3WP1XT59"
A = "54930093Q75GSEM74I71"
B = "529900XWNEXYNJ3X6T40"
OTHER = "PBLD0EJDB5FWOLXP3B76"


def _store(
    tmp_path: Path,
    bonds: list[tuple[str, str, str]],
    *,
    parents: dict[str, str] | None = None,
    skip: tuple[tuple[str, str], ...] | None = None,
    name: str = "r",
    parent_status: str = "ACTIVE",
) -> DuckStore:
    """`bonds` is (isin, lei, issuer name); `parents` maps lei -> parent lei.

    `parent_status` is the RelationshipStatus filed against every parent
    edge — the lever for the case where GLEIF has a record and no longer
    asserts it.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = DuckStore(tmp_path / f"{name}.db")
    record = Provenance(
        source_system="edgar-nport",
        source_uri="https://example.invalid/n",
        retrieved_at=KNOWN,
        method=ExtractionMethod.BULK_FILE,
        extractor_version="1",
        payload_hash="a" * 64,
    )
    store.write_provenance([record])
    facts = []
    for isin, lei, issuer in bonds:
        for field, value in {
            "gleif:lei": lei,
            "nport:assetCat": "DBT",
            "nport:name": issuer,
            "nport:maturityDt": date(2029, 6, 30),
            "nport:annualizedRt": 5.0,
            "nport:curCd": "USD",
        }.items():
            facts.append(
                Fact(
                    subject=f"isin:{isin}",
                    field=field,
                    value=value,
                    effective_from=DAY,
                    effective_to=DAY,
                    knowledge_from=KNOWN,
                    provenance_id=record.id,
                )
            )
    for child, parent in (parents or {}).items():
        for relationship in ("IS_ULTIMATELY_CONSOLIDATED_BY", "IS_DIRECTLY_CONSOLIDATED_BY"):
            if (child, relationship) in (skip or ()):
                # Used to build an entity whose direct and ultimate parents
                # differ, which is the only shape that can tell the two
                # queries apart. GLEIF states them separately and they
                # disagree for three of six entities sampled from the live
                # store, so this is the common case, not a corner.
                continue
            # An edge is *one* fact: the counterparty is in the key and the
            # RelationshipStatus is the value. The first draft wrote the
            # counterparty with a `lei:` prefix and every family came back
            # empty — the key carries the bare LEI, as GLEIF files it.
            facts.append(
                Fact(
                    subject=f"lei:{child}",
                    field=relationship_state_field(relationship, parent),
                    value=relationship_state_value(parent_status, "PUBLISHED"),
                    effective_from=DAY,
                    effective_to=DAY,
                    knowledge_from=KNOWN,
                    provenance_id=record.id,
                )
            )
    store.write_facts(facts)
    return store


class TestTheTwoRelationsAreDistinct:
    def test_another_bond_from_the_same_entity_is_same_issuer(self, tmp_path: Path) -> None:
        store = _store(
            tmp_path,
            [
                ("US0000000001", A, "BAYER US FINANCE II"),
                ("US0000000002", A, "BAYER US FINANCE II"),
            ],
        )
        related = related_securities(store, identifier="isin:US0000000001", as_of=LATER)
        assert [r.relationship for r in related.same_issuer] == ["same issuer"]
        assert related.family == ()

    def test_a_sibling_entity_is_same_parent_not_same_issuer(self, tmp_path: Path) -> None:
        """The distinction that matters. Two financing subsidiaries are one
        credit to a trader and two issuers to a curve fitter, and a screen
        that called them the same issuer would licence a curve across
        entities that file separately."""
        store = _store(
            tmp_path,
            [("US0000000001", A, "BAYER US FINANCE II"), ("US0000000002", B, "BAYER US FINANCE")],
            parents={A: PARENT, B: PARENT},
        )
        related = related_securities(store, identifier="isin:US0000000001", as_of=LATER)
        assert related.same_issuer == ()
        assert [r.relationship for r in related.family] == ["same parent"]
        assert related.family[0].lei == B

    def test_an_unrelated_issuer_is_not_included(self, tmp_path: Path) -> None:
        """A bond under a different parent must not appear merely because
        it is in the same store."""
        store = _store(
            tmp_path,
            [("US0000000001", A, "BAYER"), ("US0000000003", OTHER, "WELLS FARGO")],
            parents={A: PARENT},
        )
        with pytest.raises(NoRelationsError):
            related_securities(store, identifier="isin:US0000000001", as_of=LATER)

    def test_the_subject_is_never_its_own_relation(self, tmp_path: Path) -> None:
        store = _store(
            tmp_path,
            [("US0000000001", A, "BAYER"), ("US0000000002", A, "BAYER")],
            parents={A: PARENT},
        )
        related = related_securities(store, identifier="isin:US0000000001", as_of=LATER)
        assert all(r.identifier != "isin:US0000000001" for r in related.same_issuer)
        assert all(r.identifier != "isin:US0000000001" for r in related.family)


class TestBothCountsArePublished:
    def test_the_family_size_counts_the_registry_not_the_rows(self, tmp_path: Path) -> None:
        """The number this screen exists to get right. Three entities under
        one parent, one of which has other paper here: a reader shown only
        the row count would conclude the group was a pair."""
        store = _store(
            tmp_path,
            [("US0000000001", A, "BAYER II"), ("US0000000002", B, "BAYER")],
            parents={A: PARENT, B: PARENT, OTHER: PARENT},
        )
        related = related_securities(store, identifier="isin:US0000000001", as_of=LATER)
        assert related.family_size == 3
        assert related.reachable == 1

    def test_reachable_counts_both_relations(self, tmp_path: Path) -> None:
        store = _store(
            tmp_path,
            [
                ("US0000000001", A, "BAYER II"),
                ("US0000000002", A, "BAYER II"),
                ("US0000000003", B, "BAYER"),
            ],
            parents={A: PARENT, B: PARENT},
        )
        related = related_securities(store, identifier="isin:US0000000001", as_of=LATER)
        assert related.reachable == 2


class TestTheReasonsAreDistinguished:
    """An issuer absent from the graph, an issuer with no parent, and a
    family holding nothing else are three findings. Collapsed they read as
    'this bond is unrelated to anything', which is never true."""

    def test_no_relationship_record_says_so(self, tmp_path: Path) -> None:
        store = _store(tmp_path, [("US0000000001", A, "BAYER")])
        with pytest.raises(NoRelationsError, match="no GLEIF relationship record"):
            related_securities(store, identifier="isin:US0000000001", as_of=LATER)

    def test_a_family_with_no_other_paper_says_so(self, tmp_path: Path) -> None:
        store = _store(
            tmp_path,
            [("US0000000001", A, "BAYER")],
            parents={A: PARENT, B: PARENT},
            name="empty",
        )
        with pytest.raises(NoRelationsError, match="has other paper here"):
            related_securities(store, identifier="isin:US0000000001", as_of=LATER)

    def test_a_non_debt_holding_is_refused(self, tmp_path: Path) -> None:
        """A derivative has no issuer curve and no maturity ladder; it has
        no related-securities set either."""
        store = _store(tmp_path, [("US0000000001", A, "BAYER")], name="deriv")
        with pytest.raises(NoRelationsError, match="not a straight-debt holding"):
            related_securities(store, identifier="isin:US9999999999", as_of=LATER)

    def test_the_message_names_coverage_not_absence(self, tmp_path: Path) -> None:
        """ "No relations here" and "no relations exist" are different, and
        only the first is something this store can know."""
        store = _store(tmp_path, [("US0000000001", A, "BAYER")], name="cov")
        with pytest.raises(NoRelationsError, match="coverage of these filings"):
            related_securities(store, identifier="isin:US0000000001", as_of=LATER)


class TestTheParentRelationshipIsUsedConsistently:
    """The parent found by one relationship must have its children found by
    the same one.

    GLEIF states direct and ultimate parents separately and they disagree
    often. Taking the ultimate parent and then asking for its *direct*
    children returns a different family and one that looks entirely
    reasonable — on the live store the two answers are 133 entities and
    130, and nothing on screen would say which had been shown.

    The first version of this suite could not catch it: every fixture gave
    an entity the same direct and ultimate parent, so both queries returned
    the same set and the mutation passed ten tests.
    """

    def test_the_ultimate_family_is_used_when_the_two_parents_differ(self, tmp_path: Path) -> None:
        # A is ultimately under PARENT and directly under B. C is ultimately
        # under PARENT only. Querying PARENT's *direct* children finds
        # nothing, so a mismatched query returns an empty family.
        store = _store(
            tmp_path,
            [("US0000000001", A, "SUBSIDIARY"), ("US0000000003", OTHER, "COUSIN")],
            parents={A: PARENT, OTHER: PARENT},
            skip=((A, "IS_DIRECTLY_CONSOLIDATED_BY"), (OTHER, "IS_DIRECTLY_CONSOLIDATED_BY")),
            name="ultimate",
        )
        related = related_securities(store, identifier="isin:US0000000001", as_of=LATER)
        assert related.family_size == 2
        assert [r.identifier for r in related.family] == ["isin:US0000000003"]

    def test_the_direct_family_is_used_when_there_is_no_ultimate(self, tmp_path: Path) -> None:
        """The fallback must query the relationship it actually used."""
        store = _store(
            tmp_path,
            [("US0000000001", A, "SUBSIDIARY"), ("US0000000003", OTHER, "COUSIN")],
            parents={A: PARENT, OTHER: PARENT},
            skip=((A, "IS_ULTIMATELY_CONSOLIDATED_BY"), (OTHER, "IS_ULTIMATELY_CONSOLIDATED_BY")),
            name="direct",
        )
        related = related_securities(store, identifier="isin:US0000000001", as_of=LATER)
        assert related.family_size == 2
        assert [r.identifier for r in related.family] == ["isin:US0000000003"]


class TestALapsedParentIsNotNamed:
    """RELS must not present a parent GLEIF has stopped asserting.

    The screen's whole claim is "these bonds share an ultimate parent". If
    the parent is drawn from a superseded record the claim is false, and
    it is false in the direction that matters: the family it builds is
    some other company's.
    """

    def test_the_family_is_not_built_from_a_lapsed_record(self, tmp_path: Path) -> None:
        store = _store(
            tmp_path,
            [("US0000000001", A, "BAYER US FINANCE II"), ("US0000000002", B, "BAYER US FINANCE")],
            parents={A: PARENT, B: PARENT},
            parent_status="INACTIVE",
        )
        with pytest.raises(NoRelationsError) as caught:
            related_securities(store, identifier="isin:US0000000001", as_of=LATER)
        assert "no active record" in str(caught.value)

    def test_the_refusal_names_the_records_it_would_not_choose_between(
        self, tmp_path: Path
    ) -> None:
        store = _store(
            tmp_path,
            [("US0000000001", A, "BAYER US FINANCE II")],
            parents={A: PARENT},
            parent_status="NULL",
        )
        with pytest.raises(NoRelationsError) as caught:
            related_securities(store, identifier="isin:US0000000001", as_of=LATER)
        # Refusing is not silence: the evidence is still on screen.
        assert PARENT in str(caught.value)

    def test_a_lapsed_parent_does_not_read_as_no_parent_recorded(self, tmp_path: Path) -> None:
        """With another bond from the same issuer the screen still renders,
        and that is where the wrong word used to appear: `ultimate_parent`
        is None, so the binding said "none recorded by GLEIF" about an
        entity GLEIF has two records for."""
        store = _store(
            tmp_path,
            [("US0000000001", A, "BAYER US FINANCE II"), ("US0000000003", A, "BAYER US FINANCE")],
            parents={A: PARENT},
            parent_status="INACTIVE",
        )
        related = related_securities(store, identifier="isin:US0000000001", as_of=LATER)
        assert related.ultimate_parent is None
        assert related.parent_unresolved is not None
        assert PARENT in related.parent_unresolved
        assert related.same_issuer  # the screen still has rows to show
