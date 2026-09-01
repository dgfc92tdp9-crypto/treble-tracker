"""Single-name CDS from the SEC credit tape, against a recorded file.

`SEC_CUMULATIVE_CREDITS_2026_08_28.zip` is the real file, unmodified: 967
rows, 586 new trades, 279 reference entities. It is kept whole rather than
trimmed because the properties worth testing here are proportions — how much
of the tape is capped, how many prints carry a spread — and a hand-picked
subset would make those whatever the picker chose.

The assertion that matters most is `TestACappedNotionalIsNotASize`. 47% of
prints publish their notional as `5,000,000+`, and dividing an exactly-known
upfront payment by a floor gives an **upper bound**. Publishing that as a
level put Advanced Micro Devices on the screen at a distressed quote.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tests.ingest.test_parser_output_is_stable import check as check_parser_digest
from treble.ingest.base import RawPayload
from treble.ingest.dtcc import _rows_from_zip
from treble.ingest.dtcc_credit import (
    STANDARD_TENORS,
    DtccSdrCreditAdapter,
    best_identifier,
    credit_observations,
    curve_subject,
    entity_names,
    identifiers,
    notional_is_capped,
    reference_subject,
    spread_decimal,
)
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash

FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "dtcc" / "SEC_CUMULATIVE_CREDITS_2026_08_28.zip"
)
SOURCE = (
    "https://pddata.dtcc.com/ppd/api/report/cumulative/sec/SEC_CUMULATIVE_CREDITS_2026_08_28.zip"
)
REPORT = date(2026, 8, 28)
FETCHED = datetime(2026, 8, 29, 2, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    return _rows_from_zip(FIXTURE.read_bytes())


@pytest.fixture
def facts(tmp_path: Path) -> tuple:
    adapter = DtccSdrCreditAdapter(
        PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), report_dates=(REPORT,)
    )
    data = FIXTURE.read_bytes()
    raw = RawPayload(data=data, source_uri=SOURCE, fetched_at=FETCHED)
    return adapter.parse(raw, payload_hash(data)).facts


class TestACappedNotionalIsNotASize:
    """CLAUDE.md §6: do not treat the dissemination cap as the actual size."""

    def test_the_marker_is_the_trailing_plus(self) -> None:
        assert notional_is_capped({"Notional amount-Leg 1": "5,000,000+"})
        assert not notional_is_capped({"Notional amount-Leg 1": "5,000,000"})

    def test_nothing_else_in_this_file_says_a_trade_is_capped(
        self, rows: list[dict[str, str]]
    ) -> None:
        """`dtcc.py` reads the block-trade indicators on the rates tape.
        They are blank on all 586 rows here, so an adapter reusing them
        would find no capped trades and report every upper bound as a
        level."""
        new = [r for r in rows if r.get("Action type") == "NEWT"]
        for column in (
            "Block trade election indicator",
            "Large notional off-facility swap election indicator",
        ):
            assert not any(r.get(column) for r in new), f"{column} is populated after all"

    def test_a_capped_print_contributes_no_upfront(self, facts: tuple) -> None:
        """Advanced Micro Devices: 915,667 paid on "5,000,000 or more" is
        *at most* 18.3 points and would be 4.6 on a 20 million trade. The
        upper bound read as a distressed quote."""
        amd = [f for f in facts if "US007903BF39" in str(f.subject)]
        assert amd, "the AMD prints are no longer in the fixture; pick another capped name"
        assert not [f for f in amd if f.field == "UPFRONT_FRACTION"]

    def test_the_capped_prints_are_still_counted(self, facts: tuple) -> None:
        """A thin curve point must be visibly thin rather than quietly so."""
        amd = {f.field: f.value for f in facts if "US007903BF39" in str(f.subject)}
        assert amd["CAPPED_TRADE_COUNT"] == amd["TRADE_COUNT"]

    def test_uncapped_prints_still_produce_an_upfront(self, facts: tuple) -> None:
        """Proves the guard discriminates. One that dropped every upfront
        would pass every assertion above and leave the tape unusable."""
        upfronts = [f for f in facts if f.field == "UPFRONT_FRACTION"]
        assert upfronts
        assert all(0.0 <= f.value <= 1.0 for f in upfronts)


class TestSpreadUnits:
    """Two ISO 20022 notations appear in one file, ten thousand apart."""

    def test_decimal_notation(self) -> None:
        assert spread_decimal("0.0163", "3") == pytest.approx(0.0163)

    def test_basis_point_notation(self) -> None:
        assert spread_decimal("165", "4") == pytest.approx(0.0165)

    def test_an_unknown_notation_is_refused(self) -> None:
        """Assuming one of the two would not give a slightly wrong spread.
        It would give one wrong by a factor of 10,000."""
        assert spread_decimal("165", "9") is None
        assert spread_decimal("165", None) is None

    def test_both_notations_really_are_in_the_file(self, rows: list[dict[str, str]]) -> None:
        """Pins the reason this function exists. If the tape ever carried
        one notation only, the refusal above would be untestable in
        practice and this says so."""
        seen = {
            (r.get("Spread notation-Leg 1") or "").strip()
            for r in rows
            if r.get("Spread-Leg 1") and r.get("Action type") == "NEWT"
        }
        assert {"3", "4"} <= seen


class TestIdentifiers:
    def test_a_semicolon_list_is_unzipped(self) -> None:
        row = {
            "Underlier ID source-Leg 1": "LEI;ISIN;REDID",
            "Underlier ID-Leg 1": "F5WCUMTUM4RKZ1MAIE39;XS1410426024;FF667M",
        }
        assert identifiers(row)["REDID"] == "FF667M"

    def test_mismatched_lists_yield_nothing(self) -> None:
        """Pairing them anyway would attach one entity's code to another's."""
        assert (
            identifiers({"Underlier ID source-Leg 1": "LEI;ISIN", "Underlier ID-Leg 1": "ONE"})
            == {}
        )

    def test_the_entity_key_is_preferred_over_the_obligation(self) -> None:
        """A RED code names the reference entity; an ISIN names one of its
        bonds."""
        row = {
            "Underlier ID source-Leg 1": "ISIN;REDID",
            "Underlier ID-Leg 1": "XSSNRREFOBL0;2H6677",
        }
        assert best_identifier(row) == ("REDID", "2H6677")

    def test_sources_are_never_merged(self) -> None:
        """Two subjects that turn out to be one entity can be linked later.
        One subject that merged two entities is a wrong number."""
        assert reference_subject("REDID", "FF667M") != reference_subject("ISIN", "FF667M")

    def test_the_subject_carries_the_tenor(self) -> None:
        assert str(curve_subject("REDID", "ff667m", "5Y")) == "cds:redid:FF667M:5Y"


class TestTenors:
    def test_the_dominant_bucket_is_the_five_year(self, rows: list[dict[str, str]]) -> None:
        """CDS mature on IMM dates, so time to maturity is almost never
        whole: the biggest bucket on this file is 4.81 years, which is the
        5Y point maturing 2031-06-20. The rates adapter's 0.03 tolerance
        kept 2 prints out of 586."""
        observations = credit_observations(rows, REPORT)
        assert observations
        tenors = [o.tenor for o in observations]
        assert max(set(tenors), key=tenors.count) == 5

    def test_every_tenor_is_a_quoted_one(self, rows: list[dict[str, str]]) -> None:
        assert all(o.tenor in STANDARD_TENORS for o in credit_observations(rows, REPORT))


class TestTheParse:
    def test_it_produces_curve_points(self, facts: tuple) -> None:
        assert len({f.subject for f in facts if str(f.subject).count(":") == 3}) > 20

    def test_every_fact_is_dated_to_the_report_day(self, facts: tuple) -> None:
        assert {f.effective_from for f in facts} == {REPORT}

    def test_a_reference_entity_is_named(self, facts: tuple) -> None:
        names = [f for f in facts if f.field == "REFERENCE_ENTITY"]
        assert names and all(isinstance(f.value, str) for f in names)

    def test_the_longer_form_of_a_name_wins(self, rows: list[dict[str, str]]) -> None:
        """The file carries both "Republic of Colombia" and "REPUBLIC OF
        COLOMBIA". Neither is more correct; the one not flattened to
        capitals is the one a person can read."""
        names = entity_names(rows)
        assert any(n != n.upper() for n in names.values())

    def test_the_parse_matches_its_recorded_digest(self, tmp_path: Path) -> None:
        adapter = DtccSdrCreditAdapter(
            PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), report_dates=(REPORT,)
        )
        data = FIXTURE.read_bytes()
        batch = adapter.parse(
            RawPayload(data=data, source_uri=SOURCE, fetched_at=FETCHED), payload_hash(data)
        )
        check_parser_digest("dtcc-credit", DtccSdrCreditAdapter.parser_version, batch)
