"""TRACE adapter: credential gating and the no-silent-stub guarantee.

There is deliberately no parse test — the parser does not exist yet because
no payload has been observed (see the module docstring). These tests pin the
two properties that matter until it does: the adapter refuses to exist
without credentials, and it refuses to invent numbers.
"""

import csv
import io
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tests.ingest.test_parser_output_is_stable import check as check_parser_digest
from treble.ingest.base import RawPayload
from treble.ingest.trace import (
    SIZE_CAPPED_FIELD,
    TREASURY_DAILY_AGGREGATES,
    TraceApiAdapter,
    TraceCredentialsMissingError,
    treasury_aggregate_subject,
)
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash

FIXTURE = Path(__file__).parent.parent / "fixtures" / "trace" / "treasuryDailyAggregates.csv"
FETCHED = datetime(2026, 7, 26, 17, 30, tzinfo=UTC)


def test_refuses_to_construct_without_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FINRA_API_CLIENT_ID", raising=False)
    monkeypatch.delenv("FINRA_API_CLIENT_SECRET", raising=False)
    with pytest.raises(TraceCredentialsMissingError, match="401"):
        TraceApiAdapter(
            PayloadStore(tmp_path / "p"),
            IngestLog(tmp_path / "l.db"),
            dataset="corporateAndAgencyBondTrades",
        )


def test_parse_raises_for_unobserved_dataset(tmp_path: Path) -> None:
    adapter = TraceApiAdapter(
        PayloadStore(tmp_path / "p"),
        IngestLog(tmp_path / "l.db"),
        dataset="corporateAndAgencyBondTrades",
        client_id="test-id",
        client_secret="test-secret",  # noqa: S106
    )
    raw = RawPayload(data=b"whatever", source_uri="x", fetched_at=FETCHED)
    # Working agreement: NotImplementedError naming the spec section, never a
    # plausible wrong number, for any schema we have not actually seen.
    with pytest.raises(NotImplementedError, match=r"§8\.1\.1"):
        adapter.parse(raw, payload_hash(raw.data))


def treasury_adapter(tmp_path: Path) -> TraceApiAdapter:
    return TraceApiAdapter(
        PayloadStore(tmp_path / "p"),
        IngestLog(tmp_path / "l.db"),
        dataset=TREASURY_DAILY_AGGREGATES,
        client_id="test-id",
        client_secret="test-secret",  # noqa: S106
    )


def treasury_payload() -> RawPayload:
    return RawPayload(
        data=FIXTURE.read_bytes(),
        source_uri="https://api.finra.org/data/group/fixedIncomeMarket/name/treasuryDailyAggregates",
        fetched_at=FETCHED,
    )


class TestTreasuryAggregates:
    def test_parses_recorded_fixture(self, tmp_path: Path) -> None:
        raw = treasury_payload()
        batch = treasury_adapter(tmp_path).parse(raw, payload_hash(raw.data))
        assert batch.facts
        rows = list(csv.DictReader(io.StringIO(FIXTURE.read_text())))
        # Five measures per row, all bitemporally stamped.
        assert len(batch.facts) == len(rows) * 5
        assert all(f.knowledge_from == FETCHED for f in batch.facts)

    def test_values_match_source_and_blanks_stay_null(self, tmp_path: Path) -> None:
        rows = list(csv.DictReader(io.StringIO(FIXTURE.read_text())))
        raw = treasury_payload()
        batch = treasury_adapter(tmp_path).parse(raw, payload_hash(raw.data))
        by_key = {(f.subject, f.field, f.effective_from): f.value for f in batch.facts}
        blanks = 0
        for row in rows:
            subject = treasury_aggregate_subject(
                product_category=row["productCategory"].strip(),
                years_to_maturity=(row["yearsToMaturity"] or "").strip(),
                benchmark=(row["benchmark"] or "").strip(),
            )
            day = date.fromisoformat(row["tradeDate"])
            for column in (
                "atsInterdealerCount",
                "atsInterdealerVolume",
                "volumeWeightedAveragePrice",
            ):
                raw_value = (row[column] or "").strip()
                parsed = by_key[(subject, f"trace:{column}", day)]
                if raw_value == "":
                    # A missing VWAP must never become 0.0 (working agreement).
                    assert parsed is None
                    blanks += 1
                else:
                    assert parsed == pytest.approx(float(raw_value))
        assert blanks > 0, "fixture no longer exercises the null path"

    def test_parse_is_pure(self, tmp_path: Path) -> None:
        raw = treasury_payload()
        adapter = treasury_adapter(tmp_path)
        key = payload_hash(raw.data)
        assert adapter.parse(raw, key) == adapter.parse(raw, key)

    def test_rejects_foreign_payload(self, tmp_path: Path) -> None:
        bad = RawPayload(data=b"a,b\n1,2\n", source_uri="x", fetched_at=FETCHED)
        with pytest.raises(ValueError):
            treasury_adapter(tmp_path).parse(bad, payload_hash(bad.data))


def test_size_capped_field_is_declared() -> None:
    # The dissemination-cap flag must exist before the parser is written, so
    # the parser cannot forget it (CLAUDE.md §6, §11).
    assert SIZE_CAPPED_FIELD == "trace:size_capped"


class TestTheParserDoesNotChangeWithoutItsVersion:
    """I5: a parser is a pure function of (payload, parser version).

    Three adapters changed output while keeping their version — `dtcc-sdr`,
    `sec-nport` and `openfigi` — and each was found only after the wrong rows
    were in the store.

    Recorded from the fixture even though this source has never been
    fetched live — it is awaiting a FINRA credential. A parser guarded
    only once data arrives is unguarded for exactly the run that first
    writes rows to the store.
    """

    def test_the_parse_matches_its_recorded_digest(self, tmp_path: Path) -> None:
        raw = treasury_payload()
        batch = treasury_adapter(tmp_path).parse(raw, payload_hash(raw.data))
        check_parser_digest("trace-api", TraceApiAdapter.parser_version, batch)
