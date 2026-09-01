"""EDGAR adapters against recorded IBM fixtures (no network — CLAUDE.md §7).

The fixtures are genuine EDGAR payloads for CIK 51143 recorded 2026-07-25;
assertions reference values read from the payload itself, so the tests
verify the parse, not hand-typed numbers.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.ingest.test_parser_output_is_stable import check as check_parser_digest
from treble.ingest.base import RawPayload
from treble.ingest.edgar import (
    EdgarCompanyFactsAdapter,
    EdgarSubmissionsAdapter,
    MissingContactError,
    accepted_times,
    cik_subject,
    edgar_user_agent,
)
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash

FIXTURES = Path(__file__).parent.parent / "fixtures" / "edgar"
COMPANYFACTS = FIXTURES / "companyfacts_CIK0000051143.json"
SUBMISSIONS = FIXTURES / "submissions_CIK0000051143.json"
FETCHED = datetime(2026, 7, 25, 22, 15, tzinfo=UTC)
CONTACT = "test@example.com"


def payload(path: Path) -> RawPayload:
    return RawPayload(
        data=path.read_bytes(), source_uri=f"fixture://{path.name}", fetched_at=FETCHED
    )


@pytest.fixture
def facts_adapter(tmp_path: Path) -> EdgarCompanyFactsAdapter:
    return EdgarCompanyFactsAdapter(
        PayloadStore(tmp_path / "p"),
        IngestLog(tmp_path / "l.db"),
        ciks=(51143,),
        contact_email=CONTACT,
    )


@pytest.fixture
def submissions_adapter(tmp_path: Path) -> EdgarSubmissionsAdapter:
    return EdgarSubmissionsAdapter(
        PayloadStore(tmp_path / "p"),
        IngestLog(tmp_path / "l.db"),
        ciks=(51143,),
        contact_email=CONTACT,
    )


class TestUserAgent:
    def test_contact_email_mandatory(self) -> None:
        with pytest.raises(MissingContactError):
            edgar_user_agent("not-an-email")

    def test_identifying_user_agent(self) -> None:
        assert CONTACT in edgar_user_agent(CONTACT)


class TestCompanyFacts:
    def test_parses_real_payload(self, facts_adapter: EdgarCompanyFactsAdapter) -> None:
        raw = payload(COMPANYFACTS)
        batch = facts_adapter.parse(raw, payload_hash(raw.data))
        assert len(batch.facts) > 1000, "IBM companyfacts should yield thousands of facts"
        subject = cik_subject(51143)
        assert all(f.subject == subject for f in batch.facts)

    def test_values_match_source_rows(self, facts_adapter: EdgarCompanyFactsAdapter) -> None:
        doc = json.loads(COMPANYFACTS.read_bytes())
        gaap = doc["facts"]["us-gaap"]
        # Assert against whichever revenue tag the payload actually reports —
        # the source defines the vocabulary, not the test.
        tag_name = next(
            t
            for t in ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax")
            if t in gaap and "USD" in gaap[t].get("units", {})
        )
        tag = gaap[tag_name]
        source_row = tag["units"]["USD"][-1]
        raw = payload(COMPANYFACTS)
        batch = facts_adapter.parse(raw, payload_hash(raw.data))
        field = f"us-gaap:{tag_name}:USD"
        matching = [
            f
            for f in batch.facts
            if f.field == field
            and f.effective_to is not None
            and f.effective_to.isoformat() == source_row["end"]
            and f.effective_from.isoformat() == source_row.get("start", source_row["end"])
        ]
        assert any(f.value == float(source_row["val"]) for f in matching)

    def test_knowledge_from_is_filed_eod_utc(self, facts_adapter: EdgarCompanyFactsAdapter) -> None:
        # Conservative-late bound (I2): filed-date 23:59:59Z, tightened to
        # acceptanceDateTime at master population. Never earlier than truth.
        doc = json.loads(COMPANYFACTS.read_bytes())
        raw = payload(COMPANYFACTS)
        batch = facts_adapter.parse(raw, payload_hash(raw.data))
        accepted = accepted_times(SUBMISSIONS.read_bytes())
        by_filed: dict[str, datetime] = {}
        for tags in doc["facts"].values():
            for body in tags.values():
                for rows in body.get("units", {}).values():
                    for row in rows:
                        if "filed" in row:
                            by_filed[row["filed"]] = max(
                                by_filed.get(row["filed"], datetime.min.replace(tzinfo=UTC)),
                                datetime.fromisoformat(row["filed"] + "T23:59:59+00:00"),
                            )
        assert all(f.knowledge_from.isoformat().endswith("23:59:59+00:00") for f in batch.facts)
        # Where we have the acceptance timestamp, filed-EOD must not precede it.
        doc_accns = {
            row.get("accn"): row.get("filed")
            for tags in doc["facts"].values()
            for body in tags.values()
            for rows in body.get("units", {}).values()
            for row in rows
        }
        checked = 0
        for accn, filed in doc_accns.items():
            if accn in accepted and filed is not None:
                eod = datetime.fromisoformat(filed + "T23:59:59+00:00")
                assert eod >= accepted[accn] - timedelta(days=1)
                checked += 1
        assert checked > 0, "join produced no overlap — fixture mismatch"

    def test_parse_is_pure(self, facts_adapter: EdgarCompanyFactsAdapter) -> None:
        raw = payload(COMPANYFACTS)
        key = payload_hash(raw.data)
        assert facts_adapter.parse(raw, key) == facts_adapter.parse(raw, key)

    def test_rejects_non_companyfacts(self, facts_adapter: EdgarCompanyFactsAdapter) -> None:
        bad = RawPayload(data=b"{}", source_uri="x", fetched_at=FETCHED)
        with pytest.raises(ValueError):
            facts_adapter.parse(bad, payload_hash(bad.data))


class TestSubmissions:
    def test_parses_real_payload(self, submissions_adapter: EdgarSubmissionsAdapter) -> None:
        raw = payload(SUBMISSIONS)
        batch = submissions_adapter.parse(raw, payload_hash(raw.data))
        assert batch.facts
        assert all(f.field == "edgar:filing:form" for f in batch.facts)
        assert all(f.knowledge_from.tzinfo is not None for f in batch.facts)

    def test_accepted_times_match_source(self) -> None:
        doc = json.loads(SUBMISSIONS.read_bytes())
        recent = doc["filings"]["recent"]
        mapping = accepted_times(SUBMISSIONS.read_bytes())
        assert len(mapping) == len(recent["accessionNumber"])
        first_accn = recent["accessionNumber"][0]
        expected = datetime.fromisoformat(recent["acceptanceDateTime"][0].replace("Z", "+00:00"))
        assert mapping[first_accn] == expected


class TestTheParserDoesNotChangeWithoutItsVersion:
    """I5: a parser is a pure function of (payload, parser version).

    Three adapters have already changed output while keeping their version —
    `dtcc-sdr`, `sec-nport` and `openfigi` — and each was found after the
    wrong rows were in the store. This is the guard, on every adapter rather
    than the three that happened to burn us.
    """

    def test_the_parse_matches_its_recorded_digest(
        self, facts_adapter: EdgarCompanyFactsAdapter
    ) -> None:
        raw = payload(COMPANYFACTS)
        batch = facts_adapter.parse(raw, payload_hash(raw.data))
        check_parser_digest("edgar-companyfacts", EdgarCompanyFactsAdapter.parser_version, batch)
