"""Security master resolution (spec §9.3-9.5, WP7).

Uses the real recorded OpenFIGI and N-PORT fixtures rather than synthetic
links, so the mapping logic is proven against payloads the sources actually
returned.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from treble.core.identifiers import TUID
from treble.core.master import (
    REDISTRIBUTION_RESTRICTED_KINDS,
    figi_hierarchy,
    conflicts,
    links_from_facts,
    resolve_instrument,
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
    adapter = OpenFigiAdapter(
        PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), jobs=JOBS
    )
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
        assert REDISTRIBUTION_RESTRICTED_KINDS == {"cusip", "isin"}
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
