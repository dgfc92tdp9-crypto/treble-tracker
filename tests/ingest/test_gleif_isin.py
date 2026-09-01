"""GLEIF's ISIN-to-LEI mapping.

The adapter exists because N-PORT's issuer LEI is *filer-reported* and
GLEIF's is the issuer's own registration. On the live store they disagree
for 15 of 1,163 overlapping bonds — 1.3% — and the disagreements cluster on
subsidiaries a filer attributed to a parent. An issuer curve is fitted
across one entity's debt, so each of those was a bond on the wrong credit,
and the fit succeeded and looked smooth regardless.

The fixture is a real slice of the published file, including one of those
fifteen.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.ingest.test_parser_output_is_stable import check as check_parser_digest
from treble.ingest.base import RawPayload
from treble.ingest.gleif_isin import (
    FIELD,
    GleifIsinLeiAdapter,
    MappingUnavailableError,
    disagreements,
    lei_subject,
)
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore

FETCHED = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "gleif" / "isin_lei_slice.zip"
SLICE = FIXTURE.read_bytes()


def _adapter(tmp_path: Path, *isins: str) -> GleifIsinLeiAdapter:
    return GleifIsinLeiAdapter(
        PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), isins=isins
    )


def _parse(adapter: GleifIsinLeiAdapter, body: bytes = SLICE) -> object:
    return adapter.parse(
        RawPayload(data=body, source_uri="https://example.invalid/x", fetched_at=FETCHED),
        "0" * 64,
    )


class TestItMapsOnlyWhatWasAskedFor:
    def test_a_requested_isin_gets_its_registered_lei(self, tmp_path: Path) -> None:
        facts = _parse(_adapter(tmp_path, "US92204Q1031")).facts  # type: ignore[attr-defined]
        assert len(facts) == 1
        assert facts[0].field == FIELD
        assert facts[0].value == "00EHHQ2ZHDCFXJCPCL46"
        assert str(facts[0].subject) == "isin:US92204Q1031"

    def test_unrequested_isins_are_not_written(self, tmp_path: Path) -> None:
        """The published file is 9.1 million rows against a store holding
        1,861 bonds. Ingesting all of it would add ten million facts to
        answer questions about two thousand instruments."""
        assert len(_parse(_adapter(tmp_path, "US92204Q1031")).facts) == 1  # type: ignore[attr-defined]
        assert len(_parse(_adapter(tmp_path)).facts) == 0  # type: ignore[attr-defined]

    def test_several_isins_can_share_an_issuer(self, tmp_path: Path) -> None:
        """Two bonds from one entity is the normal case, and is exactly what
        an issuer curve is fitted across."""
        facts = _parse(_adapter(tmp_path, "US251526CV96", "US25160PAH01")).facts  # type: ignore[attr-defined]
        assert {f.value for f in facts} == {"529900HNOAA1KXQJUQ27"}
        assert len(facts) == 2

    def test_matching_is_case_insensitive(self, tmp_path: Path) -> None:
        assert len(_parse(_adapter(tmp_path, "us92204q1031")).facts) == 1  # type: ignore[attr-defined]


class TestItRefusesRatherThanGuesses:
    def test_a_changed_column_order_is_an_error(self, tmp_path: Path) -> None:
        """The two columns are both opaque alphanumeric strings, so a
        swapped header would write LEIs as ISINs and ISINs as LEIs with
        every row parsing cleanly. Nothing downstream could tell."""
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("x.csv", "FOO,BAR\n1,2\n")
        with pytest.raises(MappingUnavailableError, match="unexpected header"):
            _parse(_adapter(tmp_path, "US92204Q1031"), buf.getvalue())

    def test_the_columns_are_read_by_name_not_position(self, tmp_path: Path) -> None:
        """Proof the header check is not decorative: reversing the columns
        must still map the ISIN to the LEI rather than the reverse."""
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("x.csv", "ISIN,LEI\nUS92204Q1031,00EHHQ2ZHDCFXJCPCL46\n")
        facts = _parse(_adapter(tmp_path, "US92204Q1031"), buf.getvalue()).facts  # type: ignore[attr-defined]
        assert facts[0].value == "00EHHQ2ZHDCFXJCPCL46"

    def test_an_archive_with_no_csv_is_an_error(self, tmp_path: Path) -> None:
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("readme.txt", "nothing here")
        with pytest.raises(MappingUnavailableError, match="no CSV"):
            _parse(_adapter(tmp_path, "US92204Q1031"), buf.getvalue())


class TestTheDisagreementIsTheProduct:
    def test_it_names_the_bonds_the_two_sources_differ_on(self) -> None:
        """Returned rather than logged. A filer attributing a subsidiary's
        bond to its parent puts that bond on the wrong issuer curve, and
        the fit succeeds regardless — so this is the only place the
        discrepancy becomes visible."""
        gleif = {"US251526CV96": "529900HNOAA1KXQJUQ27", "US92204Q1031": "00EHHQ2ZHDCFXJCPCL46"}
        reported = {"US251526CV96": "7LTWFZYICNSX8D621K86", "US92204Q1031": "00EHHQ2ZHDCFXJCPCL46"}
        found = disagreements(gleif, reported)
        assert found == (("US251526CV96", "7LTWFZYICNSX8D621K86", "529900HNOAA1KXQJUQ27"),)

    def test_an_isin_only_one_source_knows_is_not_a_disagreement(self) -> None:
        """Absence and conflict are different, and only one of them says
        somebody is wrong."""
        assert disagreements({"US92204Q1031": "AAA"}, {}) == ()


class TestTheSourceIsDeclaredHonestly:
    def test_the_lei_subject_matches_the_relationship_graph(self) -> None:
        """The mapping is only useful if it lands on the same subject the
        GLEIF relationship records are keyed on — 373,125 facts that were
        unreachable from any instrument before this."""
        assert str(lei_subject("529900hnoaa1kxqjuq27")) == "lei:529900HNOAA1KXQJUQ27"

    def test_it_is_cc0_and_unrestricted(self) -> None:
        assert GleifIsinLeiAdapter.meta.redistribution_restricted is False
        assert "CC0" in GleifIsinLeiAdapter.meta.licence

    def test_it_declares_a_daily_cadence(self) -> None:
        assert GleifIsinLeiAdapter.meta.expected_cadence_days == 1.0

    def test_the_registry_knows_it(self) -> None:
        from treble.ingest.registry import all_sources

        assert "gleif-isin" in all_sources()


class TestTheParserDoesNotChangeWithoutItsVersion:
    """I5: a parser is a pure function of (payload, parser version).

    Three adapters changed output while keeping their version — `dtcc-sdr`,
    `sec-nport` and `openfigi` — and each was found only after the wrong rows
    were in the store.

    The config is *fixed* here, which is the point: a digest over one
    payload and one ISIN set changes if and only if the parser does.
    """

    def test_the_parse_matches_its_recorded_digest(self, tmp_path: Path) -> None:
        adapter = _adapter(tmp_path, "US4592001014", "US0378331005")
        batch = _parse(adapter)
        check_parser_digest("gleif-isin", GleifIsinLeiAdapter.parser_version, batch)
