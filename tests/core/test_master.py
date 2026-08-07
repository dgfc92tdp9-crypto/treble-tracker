"""Security master resolution (spec §9.3-9.5, WP7).

Uses the real recorded OpenFIGI and N-PORT fixtures rather than synthetic
links, so the mapping logic is proven against payloads the sources actually
returned.
"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import PLACEHOLDER_IDENTIFIERS, TUID
from treble.core.master import (
    REDISTRIBUTION_RESTRICTED_KINDS,
    ConflictingLinkError,
    conflicting_links,
    conflicts,
    figi_hierarchy,
    links_from_facts,
    resolve_instrument,
    resolve_link,
)
from treble.ingest.base import RawPayload
from treble.ingest.nport import NportAdapter
from treble.ingest.openfigi import OpenFigiAdapter
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash

FIXTURES = Path(__file__).parent.parent / "fixtures"
FETCHED = datetime(2026, 7, 26, 18, 0, tzinfo=UTC)
JOBS = (
    {"idType": "TICKER", "idValue": "IBM", "exchCode": "US"},
    {"idType": "ID_ISIN", "idValue": "US4592001014"},
)


def openfigi_facts(tmp_path: Path) -> list:
    results = json.loads((FIXTURES / "openfigi" / "mapping_ibm.json").read_bytes())
    envelope = json.dumps({"jobs": list(JOBS), "results": results}, sort_keys=True).encode()
    raw = RawPayload(data=envelope, source_uri="openfigi", fetched_at=FETCHED)
    adapter = OpenFigiAdapter(PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), jobs=JOBS)
    return list(adapter.parse(raw, payload_hash(raw.data)).facts)


def nport_facts(tmp_path: Path) -> list:
    data = (FIXTURES / "nport" / "nport_sample.xml").read_bytes()
    raw = RawPayload(data=data, source_uri="nport", fetched_at=FETCHED)
    adapter = NportAdapter(
        PayloadStore(tmp_path / "p2"),
        IngestLog(tmp_path / "l2.db"),
        filings=((1484018, "0002000324-26-002035"),),
        contact_email="jack_treble@icloud.com",
    )
    return list(adapter.parse(raw, payload_hash(raw.data)).facts)


class TestResolution:
    def test_isin_resolves_to_figi_from_real_mapping(self, tmp_path: Path) -> None:
        facts = openfigi_facts(tmp_path)
        links, hier = links_from_facts(facts), figi_hierarchy(facts)
        resolved = resolve_instrument(
            links,
            TUID("isin:US4592001014"),
            as_of=FETCHED + timedelta(days=1),
            hierarchy=hier,
        )
        # The spec's own example FIGI for IBM (§9.2).
        # The global share-class FIGI: one identity across 83 country listings.
        assert resolved == TUID("figi:BBG001S5S399")

    def test_venue_figi_resolves_to_its_composite(self, tmp_path: Path) -> None:
        # Spec §9.3: the hierarchy collapses to the composite level.
        facts = openfigi_facts(tmp_path)
        links, hier = links_from_facts(facts), figi_hierarchy(facts)
        us_composite = TUID("figi:BBG000BLNNH6")
        share_class = TUID("figi:BBG001S5S399")
        # The US composite carries a share class, so it resolves upward.
        assert resolve_instrument(links, us_composite, as_of=FETCHED, hierarchy=hier) == share_class
        for venue_figi in hier.share_class:
            assert (
                resolve_instrument(links, venue_figi, as_of=FETCHED, hierarchy=hier) == share_class
            )

    def test_unknown_identifier_returns_none_not_a_guess(self, tmp_path: Path) -> None:
        links = links_from_facts(openfigi_facts(tmp_path))
        assert resolve_instrument(links, TUID("isin:XX0000000000"), as_of=FETCHED) is None

    def test_resolution_is_point_in_time(self, tmp_path: Path) -> None:
        """I2: a mapping learned today must not be visible yesterday."""
        links = links_from_facts(openfigi_facts(tmp_path))
        before = FETCHED - timedelta(days=1)
        assert resolve_instrument(links, TUID("isin:US4592001014"), as_of=before) is None


class TestEvidence:
    def test_every_link_carries_provenance(self, tmp_path: Path) -> None:
        links = links_from_facts(openfigi_facts(tmp_path) + nport_facts(tmp_path))
        assert links
        assert all(link.provenance_id for link in links)
        assert all(link.knowledge_from.tzinfo is not None for link in links)

    def test_nport_contributes_isin_links(self, tmp_path: Path) -> None:
        links = links_from_facts(nport_facts(tmp_path))
        isin_links = [link for link in links if link.kind == "isin"]
        assert len(isin_links) > 50, "the recorded filing carries many ISINs"

    def test_restricted_kinds_flagged(self, tmp_path: Path) -> None:
        # Spec §9.3: CUSIP/ISIN resolve and display but never bulk-export.
        links = links_from_facts(nport_facts(tmp_path))
        assert {"cusip", "isin"} == REDISTRIBUTION_RESTRICTED_KINDS
        assert any(link.restricted for link in links)


class TestConflicts:
    def test_real_fixtures_have_no_conflicts(self, tmp_path: Path) -> None:
        # Many venue-level FIGIs per ISIN is the FIGI hierarchy working as
        # designed, not a conflict — they must collapse to one composite.
        facts = openfigi_facts(tmp_path) + nport_facts(tmp_path)
        assert conflicts(links_from_facts(facts), figi_hierarchy(facts)) == []

    def test_conflict_is_reported_not_resolved(self, tmp_path: Path) -> None:
        from treble.core.master import IdentifierLink

        shared = TUID("isin:US4592001014")
        links = [
            IdentifierLink(
                from_key=TUID("figi:BBG000BLNNH6"),
                to_key=shared,
                kind="isin",
                knowledge_from=FETCHED,
                provenance_id="a",
            ),
            IdentifierLink(
                from_key=TUID("figi:BBG000BPHFS9"),
                to_key=shared,
                kind="isin",
                knowledge_from=FETCHED,
                provenance_id="b",
            ),
        ]
        found = conflicts(links)
        assert len(found) == 1
        key, figis = found[0]
        assert key == shared
        # Both claimants surface; neither is silently preferred.
        assert len(figis) == 2


@pytest.mark.parametrize("prefix", ["figi:", "isin:", "cusip:", "lei:"])
def test_subject_key_namespacing(prefix: str) -> None:
    # Keys are namespaced so an ISIN can never collide with a CUSIP.
    assert TUID(f"{prefix}X") != TUID(f"other:{prefix}X")


class TestPlaceholdersNeverBecomeLinks:
    """Measured on the live store before this guard existed: `nport:cusip`
    of `000000000` was claimed by 246 unrelated subjects, and six subjects
    carried both a real CUSIP and the placeholder.

    `ingest/nport.py` already refused these when keying subjects. It did not
    refuse them here, so the same values that could no longer collapse a
    subject still linked every holding without a CUSIP to every other. The
    set now lives in `core.identifiers` so there is one answer.
    """

    @staticmethod
    def _fact(value: str) -> Fact:
        return Fact(
            subject="isin:US0000000001",
            field="nport:cusip",
            value=value,
            effective_from=date(2026, 6, 30),
            effective_to=date(2026, 6, 30),
            knowledge_from=datetime(2026, 7, 1, tzinfo=UTC),
            provenance_id="p" * 64,
        )

    @pytest.mark.parametrize("junk", ["000000000", "0", "N/A", "NA", "NONE", "", "  n/a  "])
    def test_a_placeholder_produces_no_link(self, junk: str) -> None:
        assert links_from_facts([self._fact(junk)]) == []

    def test_a_real_identifier_still_produces_one(self) -> None:
        links = links_from_facts([self._fact("037833100")])
        assert len(links) == 1
        assert str(links[0].to_key) == "cusip:037833100"

    def test_the_two_layers_refuse_the_same_set(self) -> None:
        """The defect was two definitions with different contents. A copy
        that drifts is how this recurs."""
        from treble.ingest.nport import _NULL_IDENTIFIERS

        assert _NULL_IDENTIFIERS is PLACEHOLDER_IDENTIFIERS


class TestConflictsAreReported:
    """The module docstring has always said conflicts are reported and never
    silently resolved. Nothing performed that check until now."""

    @staticmethod
    def _link(to: str, source: str) -> Fact:
        return Fact(
            subject="isin:US0000000001",
            field="nport:lei",
            value=to,
            effective_from=date(2026, 6, 30),
            effective_to=date(2026, 6, 30),
            knowledge_from=datetime(2026, 7, 1, tzinfo=UTC),
            provenance_id=source * 64,
        )

    def test_two_sources_disagreeing_is_reported(self) -> None:
        links = links_from_facts(
            [self._link("529900T8BM49AURSDO55", "a"), self._link("254900HROIFWPRGM1V77", "b")]
        )
        conflicts = conflicting_links(links)
        assert len(conflicts) == 1
        assert len(next(iter(conflicts.values()))) == 2

    def test_agreement_is_not_a_conflict(self) -> None:
        links = links_from_facts(
            [self._link("529900T8BM49AURSDO55", "a"), self._link("529900T8BM49AURSDO55", "b")]
        )
        assert conflicting_links(links) == {}

    def test_resolving_a_conflict_refuses_rather_than_choosing(self) -> None:
        """Preferring the newest or the most-cited source is a judgement
        this module is not entitled to make silently."""
        links = links_from_facts(
            [self._link("529900T8BM49AURSDO55", "a"), self._link("254900HROIFWPRGM1V77", "b")]
        )
        with pytest.raises(ConflictingLinkError, match="more than one"):
            resolve_link(links, TUID("isin:US0000000001"), "lei")

    def test_resolving_an_unambiguous_link_returns_it(self) -> None:
        links = links_from_facts([self._link("529900T8BM49AURSDO55", "a")])
        assert str(resolve_link(links, TUID("isin:US0000000001"), "lei")) == (
            "lei:529900T8BM49AURSDO55"
        )

    def test_an_absent_mapping_is_a_key_error_not_a_conflict(self) -> None:
        with pytest.raises(KeyError, match="no lei mapping"):
            resolve_link([], TUID("isin:US0000000001"), "lei")
