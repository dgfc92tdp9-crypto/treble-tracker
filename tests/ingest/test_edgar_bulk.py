"""Bulk XBRL from the SEC Financial Statement Data Sets.

The fixture is the real 2026q1 archive trimmed to IBM: one submission, 721
numeric rows, 370 of them dimensional. Trimmed rather than synthesised so
the traps under test are the ones the actual format contains.

The load-bearing test is `test_reconciles_with_the_companyfacts_api`. Two
independent SEC distribution channels describe the same filing; if the
parser mishandles segments, periods or units, they stop agreeing. Nothing
about the two paths is shared, so agreement is evidence rather than
tautology.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.ingest.base import RawPayload
from treble.ingest.edgar_bulk import EdgarBulkFinancialsAdapter, _accepted_utc, _period_start
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash

FIXTURES = Path(__file__).parent.parent / "fixtures" / "edgar"
ARCHIVE = FIXTURES / "financial_statement_data_sets_ibm.zip"
COMPANYFACTS = FIXTURES / "companyfacts_CIK0000051143.json"
FETCHED = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
IBM = 51143


@pytest.fixture
def facts(tmp_path: Path) -> tuple:
    adapter = EdgarBulkFinancialsAdapter(
        PayloadStore(tmp_path / "p"),
        IngestLog(tmp_path / "l.db"),
        quarters=("2026q1",),
        contact_email="test@example.com",
        ciks=frozenset({IBM}),
    )
    data = ARCHIVE.read_bytes()
    raw = RawPayload(data=data, source_uri="https://www.sec.gov/…/2026q1.zip", fetched_at=FETCHED)
    return adapter.parse(raw, payload_hash(data)).facts


def _find(facts: tuple, field: str, ends: date) -> object | None:
    matches = [f for f in facts if f.field == field and f.effective_to == ends]
    return matches[0] if len(matches) == 1 else None


class TestDimensionalRowsAreExcluded:
    """The trap that would render IBM's revenue as one business segment."""

    def test_exactly_one_consolidated_value_per_period(self, facts: tuple) -> None:
        annual = [
            f
            for f in facts
            if f.field == "us-gaap:Revenues:USD" and f.effective_to == date(2025, 12, 31)
        ]
        assert len(annual) == 1, "a segment breakdown was ingested as the consolidated total"

    def test_the_consolidated_total_is_the_one_kept(self, facts: tuple) -> None:
        """$67.535bn is the group total; the segment rows are far smaller, so
        picking a segment by mistake would be visible here."""
        fact = _find(facts, "us-gaap:Revenues:USD", date(2025, 12, 31))
        assert fact is not None
        assert fact.value == pytest.approx(67_535_000_000.0)

    def test_most_rows_in_the_fixture_are_discarded(self, facts: tuple) -> None:
        """721 rows in, 370 of them dimensional. If this ever passes with a
        count near 721 the segments filter has stopped working."""
        assert 300 < len(facts) < 420


class TestCrossSourceReconciliation:
    def test_reconciles_with_the_companyfacts_api(self, facts: tuple) -> None:
        """The same filing, from two unrelated SEC channels, must agree on
        value *and* on period — including the derived period start, which the
        archive does not state and the API does."""
        document = json.loads(COMPANYFACTS.read_bytes())
        compared = 0
        for tag in ("Revenues", "NetIncomeLoss", "Assets"):
            rows = document["facts"]["us-gaap"][tag]["units"]["USD"]
            for row in rows:
                end = date.fromisoformat(row["end"])
                bulk = _find(facts, f"us-gaap:{tag}:USD", end)
                if bulk is None:
                    continue
                start = date.fromisoformat(row.get("start") or row["end"])
                if bulk.effective_from != start:
                    continue  # a different period with the same end date
                assert bulk.value == pytest.approx(float(row["val"])), f"{tag} at {end}"
                compared += 1
        assert compared >= 3, f"only {compared} facts overlapped; the check proved little"

    def test_the_derived_period_start_matches_the_api(self, facts: tuple) -> None:
        """Directly, because a one-day disagreement would store the same
        figure twice and stop the two adapters reconciling."""
        fact = _find(facts, "us-gaap:Revenues:USD", date(2025, 12, 31))
        assert fact is not None
        assert fact.effective_from == date(2025, 1, 1)


class TestPeriodSemantics:
    def test_an_instant_starts_and_ends_on_the_same_day(self, facts: tuple) -> None:
        """qtrs=0 is a balance-sheet instant, not a zero-length flow."""
        fact = _find(facts, "us-gaap:Assets:USD", date(2025, 12, 31))
        assert fact is not None
        assert fact.effective_from == fact.effective_to == date(2025, 12, 31)

    @pytest.mark.parametrize(
        ("ends", "quarters", "starts"),
        [
            (date(2008, 6, 30), 1, date(2008, 4, 1)),
            (date(2009, 3, 31), 1, date(2009, 1, 1)),
            (date(2025, 9, 30), 2, date(2025, 4, 1)),
            (date(2025, 12, 31), 4, date(2025, 1, 1)),
            (date(2026, 3, 31), 1, date(2026, 1, 1)),
        ],
    )
    def test_period_start_rule(self, ends: date, quarters: int, starts: date) -> None:
        assert _period_start(ends, quarters) == starts


class TestKnowledgeDate:
    def test_acceptance_time_is_converted_from_eastern(self, facts: tuple) -> None:
        """IBM's 10-K was accepted at 16:07 Eastern, which is 21:07 UTC.
        Reading the stamp as UTC would move every knowledge date by five
        hours and silently reorder filings accepted near midnight."""
        fact = _find(facts, "us-gaap:Assets:USD", date(2025, 12, 31))
        assert fact is not None
        assert fact.knowledge_from == datetime(2026, 2, 24, 21, 7, tzinfo=UTC)

    def test_winter_and_summer_offsets_differ(self) -> None:
        """Eastern is UTC-5 in February and UTC-4 in July. A fixed offset
        would be wrong for half the year."""
        assert _accepted_utc("2026-02-24 16:07:00.0") == datetime(2026, 2, 24, 21, 7, tzinfo=UTC)
        assert _accepted_utc("2026-07-24 16:07:00.0") == datetime(2026, 7, 24, 20, 7, tzinfo=UTC)

    def test_an_unparseable_stamp_yields_no_knowledge_date(self) -> None:
        """Rather than a default: inventing one corrupts every point-in-time
        query against that filing."""
        assert _accepted_utc("") is None
        assert _accepted_utc("not a timestamp") is None


class TestExtensionTags:
    def test_filer_extensions_are_kept_under_their_own_namespace(self, facts: tuple) -> None:
        """CLAUDE.md requires unmapped extension tags to be surfaced, never
        dropped; the namespace makes clear they are not standard us-gaap."""
        extensions = {f.field for f in facts if f.field.startswith(f"cik{IBM:010d}:")}
        assert extensions

    def test_standard_tags_keep_their_taxonomy(self, facts: tuple) -> None:
        assert any(f.field.startswith("us-gaap:") for f in facts)


class TestParserIsPure:
    def test_parsing_twice_gives_identical_facts(self, tmp_path: Path) -> None:
        """I5: replay depends on parse being a pure function of the payload."""
        data = ARCHIVE.read_bytes()
        raw = RawPayload(data=data, source_uri="https://sec.gov/x", fetched_at=FETCHED)
        adapter = EdgarBulkFinancialsAdapter(
            PayloadStore(tmp_path / "p"),
            IngestLog(tmp_path / "l.db"),
            quarters=("2026q1",),
            contact_email="test@example.com",
            ciks=frozenset({IBM}),
        )
        first = adapter.parse(raw, payload_hash(data)).facts
        second = adapter.parse(raw, payload_hash(data)).facts
        assert first == second

    def test_every_fact_carries_provenance(self, facts: tuple) -> None:
        assert all(f.provenance_id for f in facts)

    def test_a_cik_filter_excludes_everything_else(self, tmp_path: Path) -> None:
        data = ARCHIVE.read_bytes()
        adapter = EdgarBulkFinancialsAdapter(
            PayloadStore(tmp_path / "p"),
            IngestLog(tmp_path / "l.db"),
            quarters=("2026q1",),
            contact_email="test@example.com",
            ciks=frozenset({320193}),  # Apple, absent from this fixture
        )
        raw = RawPayload(data=data, source_uri="https://sec.gov/x", fetched_at=FETCHED)
        assert adapter.parse(raw, payload_hash(data)).facts == ()
