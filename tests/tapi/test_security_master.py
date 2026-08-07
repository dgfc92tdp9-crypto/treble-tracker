"""The security master, reachable from a service (spec §9.3-9.5).

core/master.py has built links, a hierarchy and a point-in-time resolver
since WP7, and nothing called it. tapi/local.py resolved through a ticker
index while its own comment said descriptor-based resolution "needs a
security-master ... unbuilt lookup" -- one import away, tested and inert.
The reachability gate is what surfaced it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.security_master import build_security_master

MARCH = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
AUGUST = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> DuckStore:
    return DuckStore(tmp_path / "t.db")


def _write(store: DuckStore, rows: list[tuple[str, str, str, datetime]]) -> None:
    prov = Provenance(
        source_system="openfigi",
        source_uri="https://api.openfigi.com/v3/mapping",
        retrieved_at=MARCH,
        method=ExtractionMethod.API,
        extractor_version="1",
        payload_hash="0" * 64,
    )
    store.write_provenance([prov])
    store.write_facts(
        [
            Fact(
                subject=subject,
                field=field,
                value=value,
                effective_from=date(2026, 1, 1),
                effective_to=date(2026, 1, 1),
                knowledge_from=known,
                provenance_id=prov.id,
            )
            for subject, field, value, known in rows
        ]
    )


class TestItResolves:
    def test_a_cusip_resolves_to_its_figi(self, store: DuckStore) -> None:
        """The lookup tapi/local.py's comment said did not exist."""
        _write(store, [("figi:BBG000BLNNH6", "openfigi:mapped:ID_CUSIP", "037833100", MARCH)])
        master = build_security_master(store, as_of=NOW)
        assert master.resolve(TUID("cusip:037833100")) == TUID("figi:BBG000BLNNH6")

    def test_an_unknown_key_resolves_to_none(self, store: DuckStore) -> None:
        master = build_security_master(store, as_of=NOW)
        assert master.resolve(TUID("cusip:999999999")) is None

    def test_a_placeholder_never_resolves(self, store: DuckStore) -> None:
        """The defect fixed earlier today, checked from the service that
        would have surfaced it: 246 subjects claimed cusip:000000000."""
        _write(store, [("figi:BBG000BLNNH6", "openfigi:mapped:ID_CUSIP", "000000000", MARCH)])
        master = build_security_master(store, as_of=NOW)
        assert master.resolve(TUID("cusip:000000000")) is None


class TestItIsPointInTime:
    def test_resolution_as_of_a_past_date_ignores_later_evidence(self, store: DuckStore) -> None:
        """Which FIGI a CUSIP mapped to in March is a different question
        from which it maps to today. A resolver answering the second when
        asked the first makes every historical screen quietly wrong -- the
        bond right, the identity behind it today's."""
        _write(store, [("figi:BBG000BLNNH6", "openfigi:mapped:ID_CUSIP", "037833100", AUGUST)])
        assert build_security_master(store, as_of=MARCH).resolve(TUID("cusip:037833100")) is None
        assert build_security_master(store, as_of=NOW).resolve(TUID("cusip:037833100")) is not None


class TestAmbiguityIsNotAGuess:
    def test_two_figis_claiming_one_cusip_resolve_to_nothing(self, store: DuckStore) -> None:
        """A master that picked the most-cited candidate would invent an
        identity no source asserted, which §9 forbids outright."""
        _write(
            store,
            [
                ("figi:BBG000BLNNH6", "openfigi:mapped:ID_CUSIP", "037833100", MARCH),
                ("figi:BBG000B9XRY4", "openfigi:mapped:ID_CUSIP", "037833100", MARCH),
            ],
        )
        assert build_security_master(store, as_of=NOW).resolve(TUID("cusip:037833100")) is None

    def test_a_disagreement_is_distinguishable_from_no_evidence(self, store: DuckStore) -> None:
        """Both resolve to None, and a screen showing "unresolved" for both
        would hide that two sources contradict each other -- a data problem
        somebody can act on.

        The disagreement has to be *cross-source* to reach the master. Two
        values for the same field at the same knowledge time are resolved by
        the store's latest-knowledge-wins window before anything here sees
        them, which is bitemporality working: a restatement supersedes, it
        does not accumulate. What survives is two different fields asserting
        the same kind -- OpenFIGI's CUSIP against N-PORT's -- and that is
        the case worth catching, because neither source is restating the
        other and nothing arbitrates between them.
        """
        _write(
            store,
            [
                ("isin:US0378331005", "openfigi:mapped:ID_CUSIP", "037833100", MARCH),
                ("isin:US0378331005", "nport:cusip", "594918104", MARCH),
            ],
        )
        master = build_security_master(store, as_of=NOW)
        assert master.conflicts_for(TUID("isin:US0378331005")) != {}
        assert master.conflicts_for(TUID("isin:US9999999999")) == {}


class TestItReportsItsScale:
    def test_the_instrument_count_is_the_evidence_not_the_universe(self, store: DuckStore) -> None:
        _write(
            store,
            [
                ("figi:BBG000BLNNH6", "openfigi:mapped:ID_CUSIP", "037833100", MARCH),
                ("figi:BBG000BLNNH6", "openfigi:mapped:ID_ISIN", "US0378331005", MARCH),
                ("figi:BBG000B9XRY4", "openfigi:mapped:ID_CUSIP", "594918104", MARCH),
            ],
        )
        assert build_security_master(store, as_of=NOW).instrument_count == 2
