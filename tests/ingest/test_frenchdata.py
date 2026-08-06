"""The Ken French Data Library adapter, offline (CLAUDE.md §7).

This source exists to unblock `P2_3`, which was recorded as hard-blocked on
the absence of a return panel. So the tests that matter most are the ones
about what arrives: a sentinel silently read as a -99.99% daily return would
put a catastrophic fake into a factor covariance, and nothing downstream
would look wrong.
"""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.ingest.base import RawPayload
from treble.ingest.frenchdata import (
    BASE_URL,
    DATASETS,
    MISSING,
    FrenchDataAdapter,
    parse_french_csv,
)
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash

FIXTURES = Path(__file__).parent.parent / "fixtures" / "frenchdata"
FETCHED = datetime(2026, 8, 4, 9, 0, tzinfo=UTC)
FACTORS = "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
INDUSTRIES = "49_Industry_Portfolios_daily_CSV.zip"


def adapter(tmp_path: Path) -> FrenchDataAdapter:
    return FrenchDataAdapter(PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"))


def parse(tmp_path: Path, archive: str) -> tuple[Fact, ...]:
    data = (FIXTURES / archive).read_bytes()
    payload = RawPayload(data=data, source_uri=f"{BASE_URL}/{archive}", fetched_at=FETCHED)
    return adapter(tmp_path).parse(payload, payload_hash(data)).facts


class TestWhatArrives:
    def test_the_five_factors_and_the_risk_free_rate_come_through(self, tmp_path: Path) -> None:
        facts = parse(tmp_path, FACTORS)
        subjects = {f.subject for f in facts}
        assert subjects == {
            "factor:MKT_RF",
            "factor:SMB",
            "factor:HML",
            "factor:RMW",
            "factor:CMA",
            "factor:RF",
        }
        assert {f.field for f in facts} == {"TOT_RETURN"}

    def test_returns_are_decimals_not_percent(self, tmp_path: Path) -> None:
        """Every other rate in this system is a decimal. A panel mixing the
        two compounds wrongly and nothing about the number looks wrong — a
        1.5% day stored as 1.5 is a 150% day."""
        values = [f.value for f in parse(tmp_path, FACTORS) if isinstance(f.value, float)]
        assert max(abs(v) for v in values) < 0.5, (
            "a daily return above 50% suggests the percent conversion was skipped"
        )

    def test_a_daily_return_is_effective_for_exactly_its_day(self, tmp_path: Path) -> None:
        """A return measures one day and carries no validity beyond it.
        Spanning it forward would let a point-in-time read return Monday's
        return for Thursday."""
        for fact in parse(tmp_path, FACTORS):
            assert fact.effective_from == fact.effective_to

    def test_knowledge_time_is_retrieval_not_the_observation_day(self, tmp_path: Path) -> None:
        """These series are revised — a CRSP refresh restates history. Dating
        knowledge by the observation day would make a restatement
        indistinguishable from the original value (I2)."""
        facts = parse(tmp_path, FACTORS)
        assert {f.knowledge_from for f in facts} == {FETCHED}

    def test_industries_land_under_a_different_namespace_than_factors(self, tmp_path: Path) -> None:
        """An industry portfolio is an asset; a factor is not. Sharing a
        namespace would let a model regress a factor on itself."""
        subjects = {f.subject for f in parse(tmp_path, INDUSTRIES)}
        assert all(s.startswith("portfolio:") for s in subjects)
        assert len(subjects) == 49


class TestMissingDataIsAbsentNotWrong:
    def test_the_sentinels_never_become_returns(self, tmp_path: Path) -> None:
        """-99.99 and -999 are Ken French's missing markers, quoted from the
        file preamble. Read as data they are a -99.99% and a -999% day, which
        would dominate any covariance they entered."""
        values = [f.value for f in parse(tmp_path, INDUSTRIES)]
        for sentinel in MISSING:
            assert sentinel not in values
            assert sentinel / 100.0 not in values

    def test_a_missing_cell_produces_no_fact_rather_than_a_zero(self) -> None:
        """Zero is a return: it says the asset did not move. Substituting it
        for 'not reported' pulls correlations toward zero, so the model would
        understate risk in a known direction."""
        text = "\n".join(
            [
                "Some preamble.",
                "",
                ",AAA,BBB",
                "20240102,   1.00, -99.99",
                "20240103,   2.00,   0.50",
            ]
        )
        columns, rows = parse_french_csv(text)
        assert columns == ["AAA", "BBB"]
        assert rows[0][1] == [0.01, None]
        assert rows[1][1] == [0.02, 0.005]


class TestTheFileLayoutIsNotAssumed:
    def test_a_second_table_does_not_leak_into_the_first(self) -> None:
        """Several of these files hold an annual or equal-weighted table
        after the daily one. Rows from it appended to the first series would
        be returns on the wrong frequency, silently mixed in."""
        text = "\n".join(
            [
                "Average Value Weighted Returns -- Daily",
                "",
                ",AAA",
                "20240102,   1.00",
                "20240103,   2.00",
                "",
                "Average Equal Weighted Returns -- Daily",
                "",
                ",AAA",
                "20240102,   9.00",
            ]
        )
        _, rows = parse_french_csv(text)
        assert len(rows) == 2
        assert [value for _, values in rows for value in values] == [0.01, 0.02]

    def test_a_file_with_no_header_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no header row"):
            parse_french_csv("just prose\nand more prose\n")

    def test_a_file_with_no_rows_is_refused(self) -> None:
        """An empty series must not reach the store as a valid, tiny panel:
        the covariance estimator would refuse it later with a confusing
        message about observation counts."""
        with pytest.raises(ValueError, match="no daily rows"):
            parse_french_csv("preamble\n\n,AAA\n\nCopyright 2026\n")

    def test_an_unknown_archive_is_refused(self, tmp_path: Path) -> None:
        payload = RawPayload(data=b"x", source_uri=f"{BASE_URL}/Nope.zip", fetched_at=FETCHED)
        with pytest.raises(ValueError, match="not a known Ken French dataset"):
            adapter(tmp_path).parse(payload, payload_hash(b"x"))

    def test_an_unknown_dataset_is_refused_at_construction(self, tmp_path: Path) -> None:
        """Before the network, not after: a typo in a dataset name would
        otherwise be a 404 on a university web server."""
        with pytest.raises(ValueError, match="unknown Ken French dataset"):
            FrenchDataAdapter(
                PayloadStore(tmp_path / "p"),
                IngestLog(tmp_path / "l.db"),
                datasets=("F-F_Nonexistent_CSV.zip",),
            )

    def test_an_archive_with_more_than_one_member_is_refused(self, tmp_path: Path) -> None:
        """Reading `namelist()[0]` from a multi-file archive would pick one
        table by zip ordering and call it the dataset."""
        import io

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("a.csv", ",AAA\n20240102,1.00\n")
            archive.writestr("b.csv", ",AAA\n20240102,2.00\n")
        data = buffer.getvalue()
        payload = RawPayload(data=data, source_uri=f"{BASE_URL}/{FACTORS}", fetched_at=FETCHED)
        with pytest.raises(ValueError, match="expected one CSV"):
            adapter(tmp_path).parse(payload, payload_hash(data))


class TestLicenceHandling:
    def test_the_source_is_marked_redistribution_restricted(self) -> None:
        """The files are stamped 'Copyright Eugene F. Fama and Kenneth R.
        French'. The flag is what makes the bulk-export guard withhold them,
        so this data can be analysed here without being re-published."""
        assert FrenchDataAdapter.meta.redistribution_restricted is True

    def test_the_export_guard_actually_withholds_it(self) -> None:
        """The flag being set is not the point; the flag being *read* is.
        `redistribution_restricted` was declared on thirteen adapters and
        read by nothing until the guard was built."""
        from treble.ingest.registry import restricted_source_ids

        assert FrenchDataAdapter.meta.source_id in restricted_source_ids()

    def test_the_licence_records_what_was_checked(self) -> None:
        """Two sources have already been refused on this project over access
        terms. What was checked, and when, has to survive in the metadata
        rather than in a commit message."""
        licence = FrenchDataAdapter.meta.licence
        assert "robots.txt" in licence
        assert "2026-08-04" in licence

    def test_the_rate_limit_is_gentle(self) -> None:
        """A university web server hosting a public good, not an API with a
        published quota."""
        assert FrenchDataAdapter.meta.rate_limit_per_second == 0.5


class TestReplay:
    def test_replay_reproduces_the_same_facts(self, tmp_path: Path) -> None:
        """I5. `parse` is a pure function of (payload, parser_version), so
        re-parsing from the log must give byte-identical facts."""
        payloads, log = PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db")
        source = FrenchDataAdapter(payloads, log)
        data = (FIXTURES / FACTORS).read_bytes()
        key = payloads.put(data)
        log.append(
            source=source.meta.source_id,
            payload_hash=key,
            source_uri=f"{BASE_URL}/{FACTORS}",
            fetched_at=FETCHED,
            parser_version=source.parser_version,
        )
        replayed = list(source.replay())
        assert len(replayed) == 1
        original = parse(tmp_path / "other", FACTORS)
        assert replayed[0].facts == original


def test_every_declared_dataset_has_a_fixture() -> None:
    """A dataset the adapter can fetch but no test ever parses is a parser
    nobody has run. The three shipped here are the three declared."""
    missing = sorted(name for name in DATASETS if not (FIXTURES / name).exists())
    assert not missing, f"no offline fixture for: {', '.join(missing)}"
