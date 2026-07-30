"""Knowledge dates at acceptance-time resolution (I2).

`companyfacts` states a filing *date*, not a time, so every filing made on
one day collapsed to a single knowledge instant. Apple filed twice on
2015-01-28 reporting 4,000,000,000 and 4,033,000,000 for the same period;
at day resolution the pair could not be ordered, and latest-knowledge-wins
had two equally-good answers. Eleven such groups existed in the store.

Joining each row's own accession to its acceptance time separates them —
21:39:32 and 21:48:46, nine minutes apart — and the conflict becomes an
ordinary restatement.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from treble.ingest.base import RawPayload
from treble.ingest.edgar import (
    EdgarCompanyFactsAdapter,
    accepted_times,
    submission_pages,
)
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash

FETCHED = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
EARLY, LATE = "0001-15-000001", "0001-15-000002"

COMPANYFACTS = json.dumps(
    {
        "cik": 320193,
        "facts": {
            "us-gaap": {
                "UnrecognizedTaxBenefits": {
                    "units": {
                        "USD": [
                            {
                                "end": "2014-09-27",
                                "val": 4_000_000_000,
                                "filed": "2015-01-28",
                                "accn": EARLY,
                            },
                            {
                                "end": "2014-09-27",
                                "val": 4_033_000_000,
                                "filed": "2015-01-28",
                                "accn": LATE,
                            },
                        ]
                    }
                }
            }
        },
    }
).encode()

ACCEPTED = {
    EARLY: datetime(2015, 1, 28, 21, 39, 32, tzinfo=UTC),
    LATE: datetime(2015, 1, 28, 21, 48, 46, tzinfo=UTC),
}


def _parse(tmp_path: Path, accepted: dict[str, datetime] | None) -> tuple:
    adapter = EdgarCompanyFactsAdapter(
        PayloadStore(tmp_path / "p"),
        IngestLog(tmp_path / "l.db"),
        ciks=(320193,),
        contact_email="jack_treble@icloud.com",
        accepted=accepted,
    )
    raw = RawPayload(data=COMPANYFACTS, source_uri="https://sec.gov/cf", fetched_at=FETCHED)
    return adapter.parse(raw, payload_hash(COMPANYFACTS)).facts


class TestSameDayFilingsAreOrdered:
    def test_without_the_join_the_two_filings_collapse(self, tmp_path: Path) -> None:
        """The bug, pinned. Both land on one knowledge instant, so the store
        holds two values that cannot be ordered."""
        stamps = {f.knowledge_from for f in _parse(tmp_path, None)}
        assert len(stamps) == 1

    def test_with_the_join_they_separate(self, tmp_path: Path) -> None:
        facts = sorted(_parse(tmp_path, ACCEPTED), key=lambda f: f.knowledge_from)
        assert [f.knowledge_from for f in facts] == [ACCEPTED[EARLY], ACCEPTED[LATE]]

    def test_the_later_filing_wins(self, tmp_path: Path) -> None:
        """Which is the point: a deterministic answer, not an arbitrary one."""
        facts = sorted(_parse(tmp_path, ACCEPTED), key=lambda f: f.knowledge_from)
        assert facts[-1].value == 4_033_000_000

    def test_an_unknown_accession_falls_back_to_the_filing_date(self, tmp_path: Path) -> None:
        """So the adapter still works before submissions are ingested — at
        the old resolution rather than failing."""
        facts = _parse(tmp_path, {})
        assert all(f.knowledge_from.hour == 23 for f in facts)


class TestOlderFilingsAreReachable:
    def test_pages_are_listed_from_the_main_document(self) -> None:
        """`filings.recent` caps at 1000. Apple's stops at 2015-05-29, and
        every conflict was older than that — so the pages are not optional."""
        document = json.dumps(
            {"filings": {"recent": {}, "files": [{"name": "CIK0000320193-submissions-001.json"}]}}
        ).encode()
        assert submission_pages(document) == ("CIK0000320193-submissions-001.json",)

    def test_a_page_parses_without_the_filings_wrapper(self) -> None:
        """Pages carry the arrays bare, unlike the main document."""
        page = json.dumps(
            {
                "accessionNumber": [EARLY],
                "acceptanceDateTime": ["2015-01-28T21:39:32.000Z"],
            }
        ).encode()
        assert accepted_times(page) == {EARLY: ACCEPTED[EARLY]}

    def test_no_pages_is_not_an_error(self) -> None:
        assert submission_pages(json.dumps({"filings": {"recent": {}}}).encode()) == ()
