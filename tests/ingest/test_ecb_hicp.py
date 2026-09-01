"""ECB HICP (spec §9.1, §12.1).

The inflation swap pricer shipped with no index to price against, and the
reachability sweep recorded it as data-blocked rather than unwired. This is
the data, on recorded fixtures.

What is tested is the two decisions this adapter makes that a later reader
could not recover: that a monthly observation spans its month rather than a
day, and that the level and the year-on-year rate are kept apart.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tests.ingest.test_parser_output_is_stable import check as check_parser_digest
from treble.ingest.base import RawPayload
from treble.ingest.ecb_hicp import (
    INDEX_FIELD,
    RATE_FIELD,
    SUBJECT,
    EcbHicpAdapter,
)
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ecb"
FETCHED = datetime(2026, 8, 7, 9, 0, tzinfo=UTC)
HASH = PayloadHash("0" * 64)


@pytest.fixture
def adapter(tmp_path: Path) -> EcbHicpAdapter:
    return EcbHicpAdapter(PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"))


def _payload(name: str, key: str) -> RawPayload:
    return RawPayload(
        data=(FIXTURES / name).read_bytes(),
        source_uri=f"https://data-api.ecb.europa.eu/service/data/ICP/{key}",
        fetched_at=FETCHED,
    )


class TestAMonthlyObservationSpansItsMonth:
    def test_the_period_becomes_a_full_month(self, adapter: EcbHicpAdapter) -> None:
        """`2025-10` describes October, not the first of it. Collapsing it
        to one day would make a point-in-time read mid-month miss an
        observation that was true of that day."""
        batch = adapter.parse(_payload("hicp_index.csv", "M.U2.N.000000.4.INX"), HASH)
        january = next(f for f in batch.facts if f.effective_from == date(2024, 1, 1))
        assert january.effective_to == date(2024, 1, 31)

    def test_february_in_a_leap_year_ends_on_the_29th(self, adapter: EcbHicpAdapter) -> None:
        """A hardcoded 28 would silently lose a day every four years."""
        batch = adapter.parse(_payload("hicp_index.csv", "M.U2.N.000000.4.INX"), HASH)
        february = next((f for f in batch.facts if f.effective_from == date(2024, 2, 1)), None)
        assert february is not None
        assert february.effective_to == date(2024, 2, 29)


class TestTheLevelAndTheRateStayApart:
    def test_the_index_is_stored_as_a_level(self, adapter: EcbHicpAdapter) -> None:
        """A zero-coupon inflation swap pays I_T/I_0 - 1 off levels. Storing
        only the rate would force every consumer to reconstruct a level from
        a percentage and a base nobody recorded."""
        batch = adapter.parse(_payload("hicp_index.csv", "M.U2.N.000000.4.INX"), HASH)
        assert {f.field for f in batch.facts} == {INDEX_FIELD}
        assert all(100.0 < float(f.value) < 200.0 for f in batch.facts)

    def test_the_rate_is_stored_under_its_own_field(self, adapter: EcbHicpAdapter) -> None:
        """129.7 and 2.1 are both plausible numbers and mean entirely
        different things."""
        batch = adapter.parse(_payload("hicp_rate.csv", "M.U2.N.000000.4.ANR"), HASH)
        assert {f.field for f in batch.facts} == {RATE_FIELD}
        assert all(-5.0 < float(f.value) < 15.0 for f in batch.facts)

    def test_both_land_on_one_subject(self, adapter: EcbHicpAdapter) -> None:
        index = adapter.parse(_payload("hicp_index.csv", "M.U2.N.000000.4.INX"), HASH)
        rate = adapter.parse(_payload("hicp_rate.csv", "M.U2.N.000000.4.ANR"), HASH)
        assert {f.subject for f in (*index.facts, *rate.facts)} == {SUBJECT}


class TestItRefusesRatherThanInvents:
    def test_a_non_monthly_period_produces_no_fact(self, adapter: EcbHicpAdapter) -> None:
        """ECB serves other frequencies from the same dataflow, and reading
        a quarterly period as a month would file Q4 under October."""
        body = (
            b"KEY,TIME_PERIOD,OBS_VALUE\n"
            b"ICP.Q.U2.N.000000.4.INX,2025-Q4,129.7\n"
            b"ICP.M.U2.N.000000.4.INX,2025-10,129.7\n"
        )
        batch = adapter.parse(RawPayload(data=body, source_uri="x", fetched_at=FETCHED), HASH)
        assert len(batch.facts) == 1
        assert batch.facts[0].effective_from == date(2025, 10, 1)

    def test_a_series_that_parses_to_nothing_raises(self, adapter: EcbHicpAdapter) -> None:
        body = b"KEY,TIME_PERIOD,OBS_VALUE\n"
        with pytest.raises(ValueError, match="no observations"):
            adapter.parse(RawPayload(data=body, source_uri="x", fetched_at=FETCHED), HASH)

    def test_the_knowledge_date_is_the_retrieval(self, adapter: EcbHicpAdapter) -> None:
        """HICP for October is published in November, and the payload
        carries no per-observation publication timestamp. Inventing one from
        a release-calendar rule would be a guess wearing a fact's clothes."""
        batch = adapter.parse(_payload("hicp_index.csv", "M.U2.N.000000.4.INX"), HASH)
        assert {f.knowledge_from for f in batch.facts} == {FETCHED}


class TestTheParserDoesNotChangeWithoutItsVersion:
    """I5: a parser is a pure function of (payload, parser version).

    Three adapters have already changed output while keeping their version —
    `dtcc-sdr` (227 against 234 for one payload), `sec-nport` (two subject
    schemes) and `openfigi` (a moving effective date). Each was found after
    the wrong rows were in the store. This is the guard, and it is on every
    adapter rather than the three that happened to burn us.
    """

    def test_the_parse_matches_its_recorded_digest(self, adapter: EcbHicpAdapter) -> None:
        batch = adapter.parse(_payload("hicp_index.csv", "M.U2.N.000000.4.INX"), HASH)
        check_parser_digest("ecb-hicp", EcbHicpAdapter.parser_version, batch)
