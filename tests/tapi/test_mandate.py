"""The seam between N-PORT holdings and the compliance rules.

The engine is unit-tested against synthetic portfolios in
`tests/compliance/`. This tests the part that turns stored facts into
`Holding`s, and specifically the issuer identity — which is where a
concentration rule can be made to *understate* concentration, the one
direction that matters.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from tests.storebuilder import split_holding
from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.mandate import holdings_from_store

NOW = datetime(2026, 8, 24, tzinfo=UTC)
KNOWN = datetime(2026, 8, 1, tzinfo=UTC)
REPORT = date(2026, 5, 31)


def _store(tmp_path: Path, holdings: dict[str, dict[str, object]]) -> DuckStore:
    store = DuckStore(tmp_path / "m.db")
    record = Provenance(
        source_system="sec-nport",
        source_uri="https://example.invalid/nport",
        retrieved_at=KNOWN,
        method=ExtractionMethod.BULK_FILE,
        extractor_version="1",
        payload_hash="a" * 64,
    )
    store.write_provenance([record])
    store.write_facts(
        [
            Fact(
                subject=TUID(subject),
                field=field,
                value=value,  # type: ignore[arg-type]
                effective_from=REPORT,
                effective_to=None,
                knowledge_from=KNOWN,
                provenance_id=record.id,
            )
            for subject_fields in holdings.items()
            for subject, field, value in split_holding(subject_fields[0], subject_fields[1])
        ]
    )
    return store


class TestIssuerIdentity:
    def test_a_holding_with_no_lei_is_keyed_by_its_issuer_name(self, tmp_path: Path) -> None:
        """241 of the 242 live holdings without an LEI carry a name. Not
        grouping them at all left the concentration rule unevaluable."""
        store = _store(
            tmp_path,
            {
                "isin:AAA": {
                    "nport:valUSD": 100.0,
                    "nport:name": "Emaar Development PJSC",
                    "nport:assetCat": "EC",
                }
            },
        )
        (holding,) = holdings_from_store(store, as_of=NOW)
        assert holding.issuer == "name:EMAAR DEVELOPMENT PJSC"

    def test_one_issuer_seen_both_ways_becomes_one_group(self, tmp_path: Path) -> None:
        """**The dangerous direction.** An issuer holding one bond under
        its LEI and one share under its name alone would otherwise split
        into two groups, and a 12% combined exposure would read as two 6%
        positions — a concentration rule understating concentration.

        The name-to-LEI mapping is learned from the holding that carries
        both and applied to the one that does not.
        """
        store = _store(
            tmp_path,
            {
                "isin:BOND": {
                    "nport:valUSD": 100.0,
                    "nport:name": "Acme Corp",
                    "nport:lei": "5493001KJTIIGC8Y1R12",
                    "nport:assetCat": "DBT",
                },
                "isin:SHARE": {
                    "nport:valUSD": 100.0,
                    "nport:name": "Acme Corp",
                    "nport:assetCat": "EC",
                },
            },
        )
        issuers = {h.issuer for h in holdings_from_store(store, as_of=NOW)}
        assert issuers == {"5493001KJTIIGC8Y1R12"}, "one issuer split into two groups"

    def test_names_are_matched_case_insensitively(self, tmp_path: Path) -> None:
        store = _store(
            tmp_path,
            {
                "isin:A": {
                    "nport:valUSD": 100.0,
                    "nport:name": "acme corp",
                    "nport:lei": "5493001KJTIIGC8Y1R12",
                },
                "isin:B": {"nport:valUSD": 100.0, "nport:name": "ACME CORP "},
            },
        )
        assert len({h.issuer for h in holdings_from_store(store, as_of=NOW)}) == 1

    def test_different_issuers_are_not_merged(self, tmp_path: Path) -> None:
        """The matching is deliberately shallow — case and whitespace
        only. Stripping suffixes would merge `GPT Group` into `GPT`, and
        this exists to avoid understating concentration, so it errs
        towards splitting."""
        store = _store(
            tmp_path,
            {
                "isin:A": {"nport:valUSD": 100.0, "nport:name": "GPT Group"},
                "isin:B": {"nport:valUSD": 100.0, "nport:name": "GPT"},
            },
        )
        assert len({h.issuer for h in holdings_from_store(store, as_of=NOW)}) == 2

    def test_a_holding_with_neither_lei_nor_name_has_no_issuer(self, tmp_path: Path) -> None:
        """Still unattributed, and the rule's bound decides whether that
        matters — inventing an identity here would be worse."""
        store = _store(tmp_path, {"isin:A": {"nport:valUSD": 100.0, "nport:assetCat": "EC"}})
        (holding,) = holdings_from_store(store, as_of=NOW)
        assert holding.issuer is None

    def test_gleif_lei_is_preferred_over_the_filers(self, tmp_path: Path) -> None:
        """GLEIF is the registry; the filer's LEI is a transcription of
        it, and 15 of 1,163 disagree on the live store."""
        store = _store(
            tmp_path,
            {
                "isin:A": {
                    "nport:valUSD": 100.0,
                    "nport:lei": "5493001KJTIIGC8Y1R12",
                    "gleif:lei": "213800WAVVOPS85N2205",
                }
            },
        )
        (holding,) = holdings_from_store(store, as_of=NOW)
        assert holding.issuer == "213800WAVVOPS85N2205"


class TestWhatReachesTheRules:
    def test_a_holding_with_no_mark_is_dropped(self, tmp_path: Path) -> None:
        """A position with no market value cannot be weighted, and
        guessing one would put a number into every percentage rule."""
        store = _store(
            tmp_path,
            {
                "isin:A": {"nport:valUSD": 100.0, "nport:name": "X"},
                "isin:B": {"nport:name": "Y"},
            },
        )
        assert [h.identifier for h in holdings_from_store(store, as_of=NOW)] == ["isin:A"]

    def test_currency_and_category_reach_the_rules(self, tmp_path: Path) -> None:
        store = _store(
            tmp_path,
            {
                "isin:A": {
                    "nport:valUSD": 100.0,
                    "nport:curCd": "AED",
                    "nport:assetCat": "EC",
                    "nport:maturityDt": date(2040, 1, 1),
                }
            },
        )
        (holding,) = holdings_from_store(store, as_of=NOW)
        assert (holding.currency, holding.asset_category) == ("AED", "EC")
        assert holding.maturity == date(2040, 1, 1)

    def test_rating_is_always_none(self, tmp_path: Path) -> None:
        """No rating source this repository may use has been found, and
        inventing one is the difference between a rule that fails honestly
        and a report that lies."""
        store = _store(tmp_path, {"isin:A": {"nport:valUSD": 100.0, "nport:name": "X"}})
        assert holdings_from_store(store, as_of=NOW)[0].rating is None
