"""`DDIS` — the issuer maturity ladder.

Two defects were found building this, and both are the kind that produce a
plausible screen rather than an error.

The date was chosen by counting an issuer's *holdings* and then filtering to
straight debt, so a day whose seven holdings were all derivatives beat a day
with six bonds — the ladder came back empty for an issuer that had one.
Choosing by a count that is not the count that matters is the same defect as
`SWPM`'s basis tab picking the newest day the discount/forecast *pair* built
on when it needed a third curve.

The coupon was treated as a decimal. `nport:annualizedRt` ranges 0 to 12.5
on the live store with a median of 4.625, so it is a rate in percent, and a
ladder rendering it as a decimal showed 546% coupons.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.debt import (
    BUCKETS,
    DebtDistributionUnavailableError,
    debt_distribution,
)

KNOWN = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
LEI = "213800LBQA1Y9L22JB70"
DAY = date(2026, 3, 31)


def _store(tmp_path: Path, bonds: list[dict[str, object]]) -> DuckStore:
    store = DuckStore(tmp_path / "d.db")
    record = Provenance(
        source_system="edgar-nport",
        source_uri="https://example.invalid/nport",
        retrieved_at=KNOWN,
        method=ExtractionMethod.BULK_FILE,
        extractor_version="1",
        payload_hash="a" * 64,
    )
    store.write_provenance([record])
    facts = []
    for bond in bonds:
        day = bond.get("day", DAY)
        for field, value in bond.items():
            if field in {"isin", "day"}:
                continue
            facts.append(
                Fact(
                    subject=f"isin:{bond['isin']}",
                    field=field,
                    value=value,
                    effective_from=day,  # type: ignore[arg-type]
                    effective_to=day,  # type: ignore[arg-type]
                    knowledge_from=KNOWN,
                    provenance_id=record.id,
                )
            )
    store.write_facts(facts)
    return store


def _bond(isin: str, *, years: int, coupon: float = 5.0, **extra: object) -> dict[str, object]:
    return {
        "isin": isin,
        "gleif:lei": LEI,
        "nport:assetCat": "DBT",
        "nport:curCd": "USD",
        "nport:name": "BARCLAYS PLC",
        "nport:maturityDt": date(DAY.year + years, DAY.month, DAY.day),
        "nport:annualizedRt": coupon,
        "nport:valUSD": 1_000_000.0,
        **extra,
    }


class TestTheLadder:
    def test_bonds_land_in_the_right_buckets(self, tmp_path: Path) -> None:
        store = _store(
            tmp_path,
            [
                _bond("US0000000001", years=2),
                _bond("US0000000002", years=4),
                _bond("US0000000003", years=12),
            ],
        )
        profile = debt_distribution(store, lei=LEI, as_of=LATER)
        counts = {b.label: b.bonds for b in profile.buckets}
        assert counts == {"0-1y": 0, "1-3y": 1, "3-5y": 1, "5-7y": 0, "7-10y": 0, "10y+": 1}

    def test_every_bucket_is_returned_even_when_empty(self, tmp_path: Path) -> None:
        """A ladder with rungs missing is read as a ladder with no debt at
        those maturities only if the rungs are shown. Dropping them makes an
        issuer with a gap look like one with a shorter profile."""
        store = _store(tmp_path, [_bond("US0000000001", years=2)])
        assert len(debt_distribution(store, lei=LEI, as_of=LATER).buckets) == len(BUCKETS)

    def test_the_coupon_is_a_percentage_not_a_decimal(self, tmp_path: Path) -> None:
        """nport:annualizedRt ranges 0 to 12.5 on the live store. Treated as
        a decimal it renders as 546%, which looks like a data error rather
        than a units error and would be chased in the wrong place."""
        store = _store(tmp_path, [_bond("US0000000001", years=2, coupon=5.25)])
        bucket = next(
            b for b in debt_distribution(store, lei=LEI, as_of=LATER).buckets if b.label == "1-3y"
        )
        assert bucket.mean_coupon_pct == pytest.approx(5.25)

    def test_the_coupon_mean_is_unweighted(self, tmp_path: Path) -> None:
        """Weighting by held face would weight by which fund happened to
        file, which is not a property of the debt."""
        store = _store(
            tmp_path,
            [
                _bond("US0000000001", years=2, coupon=4.0, **{"nport:valUSD": 9_000_000.0}),
                _bond("US0000000002", years=2, coupon=6.0, **{"nport:valUSD": 1_000_000.0}),
            ],
        )
        bucket = next(
            b for b in debt_distribution(store, lei=LEI, as_of=LATER).buckets if b.label == "1-3y"
        )
        assert bucket.mean_coupon_pct == pytest.approx(5.0)


class TestTheReportDateIsChosenByUsableBonds:
    def test_a_day_of_derivatives_does_not_beat_a_day_of_bonds(self, tmp_path: Path) -> None:
        """The defect this class exists for. Counting holdings and then
        filtering picked the later, larger, useless day and returned an
        empty ladder for an issuer with six bonds a quarter earlier."""
        later = date(2026, 8, 10)
        store = _store(
            tmp_path,
            [_bond(f"US000000000{i}", years=2) for i in range(1, 4)]
            + [
                {
                    "isin": f"US000000010{i}",
                    "gleif:lei": LEI,
                    "nport:assetCat": "DE",
                    "nport:curCd": "USD",
                    "day": later,
                }
                for i in range(1, 8)
            ],
        )
        profile = debt_distribution(store, lei=LEI, as_of=LATER)
        assert profile.report_date == DAY
        assert profile.total_bonds == 3

    def test_an_explicit_date_is_honoured(self, tmp_path: Path) -> None:
        store = _store(tmp_path, [_bond("US0000000001", years=2)])
        assert debt_distribution(store, lei=LEI, as_of=LATER, report_date=DAY).report_date == DAY

    def test_a_date_with_no_holdings_is_refused(self, tmp_path: Path) -> None:
        store = _store(tmp_path, [_bond("US0000000001", years=2)])
        with pytest.raises(DebtDistributionUnavailableError, match="no holdings reported"):
            debt_distribution(store, lei=LEI, as_of=LATER, report_date=date(2020, 1, 1))


class TestWhatIsExcludedIsCounted:
    def test_securitisations_are_not_on_a_corporate_ladder(self, tmp_path: Path) -> None:
        """issuerCat cannot do this job — a securitisation vehicle IS a
        corporation and filers classify it CORP. On the live store the
        asset category moved 171 of 460 debt holdings out."""
        store = _store(
            tmp_path,
            [
                _bond("US0000000001", years=2),
                _bond("US0000000002", years=2, **{"nport:assetCat": "ABS-MBS"}),
            ],
        )
        profile = debt_distribution(store, lei=LEI, as_of=LATER)
        assert profile.total_bonds == 1
        assert any("ABS-MBS" in reason for _, reason in profile.excluded)

    def test_a_bond_past_its_maturity_is_excluded_not_bucketed(self, tmp_path: Path) -> None:
        """A matured bond in a forward profile is a stale holding record or
        a default. Bucketed into 0-1y it would read as debt coming due."""
        store = _store(
            tmp_path,
            [_bond("US0000000001", years=2), _bond("US0000000002", years=-3)],
        )
        profile = debt_distribution(store, lei=LEI, as_of=LATER)
        assert profile.total_bonds == 1
        assert any("matured" in reason for _, reason in profile.excluded)

    def test_an_issuer_with_nothing_usable_is_an_error_not_an_empty_ladder(
        self, tmp_path: Path
    ) -> None:
        """Six zeroes reads as an issuer that has repaid everything."""
        store = _store(tmp_path, [_bond("US0000000001", years=2, **{"nport:assetCat": "ABS-MBS"})])
        with pytest.raises(DebtDistributionUnavailableError, match="straight-debt"):
            debt_distribution(store, lei=LEI, as_of=LATER)

    def test_an_unknown_issuer_says_what_the_ladder_cannot_see(self, tmp_path: Path) -> None:
        """Absent from every filing is not debt-free, and the message has
        to say so or a reader concludes the issuer has no bonds."""
        store = _store(tmp_path, [_bond("US0000000001", years=2)])
        with pytest.raises(DebtDistributionUnavailableError, match="invisible to it"):
            debt_distribution(store, lei="NOTALEI", as_of=LATER)


class TestTheRegistryIdentifiesTheIssuer:
    def test_gleif_wins_over_the_filer(self, tmp_path: Path) -> None:
        """The two disagree on 1.3% of bonds, clustered on subsidiaries
        attributed to a parent — exactly the error that would put another
        company's bonds on this ladder."""
        store = _store(
            tmp_path,
            [
                _bond("US0000000001", years=2),
                _bond("US0000000002", years=2, **{"gleif:lei": "OTHERLEI", "nport:lei": LEI}),
            ],
        )
        assert debt_distribution(store, lei=LEI, as_of=LATER).total_bonds == 1

    def test_the_filer_is_used_where_gleif_is_silent(self, tmp_path: Path) -> None:
        """63% of our ISINs are in GLEIF's file. Ignoring nport:lei for the
        rest would drop a third of the universe off every ladder."""
        bond = _bond("US0000000003", years=2)
        del bond["gleif:lei"]
        bond["nport:lei"] = LEI
        assert debt_distribution(_store(tmp_path, [bond]), lei=LEI, as_of=LATER).total_bonds == 1


class TestTheBinding:
    def test_the_ladder_totals(self, tmp_path: Path) -> None:
        from treble.core.identifiers import isin_from_cusip

        isin = isin_from_cusip("000000000")
        bond = _bond(isin, years=2)
        rows = LocalTapiRows(_store(tmp_path, [bond]), isin)
        assert rows[-1][0] == "TOTAL"
        assert rows[-1][1] == 1

    def test_the_method_tab_says_what_held_means(self, tmp_path: Path) -> None:
        from treble.core.identifiers import isin_from_cusip

        isin = isin_from_cusip("000000000")
        rows = dict(
            LocalTapiRows(_store(tmp_path, [_bond(isin, years=2)]), isin, "sys:ddis_method")
        )  # type: ignore[arg-type]
        assert "not the amount outstanding" in str(rows["What HELD means"])
        assert "most usable bonds" in str(rows["Date chosen by"])


def LocalTapiRows(  # noqa: N802 - reads as a constructor at the call sites above
    store: DuckStore, isin: str, binding: str = "sys:ddis_ladder"
) -> tuple[tuple[object, ...], ...]:
    from treble.core.identifiers import SecurityQuery, YellowKey
    from treble.tapi.local import LocalTapi

    return LocalTapi(store).series(
        SecurityQuery(ticker=isin, key=YellowKey.CORP, venue=None, descriptor=None),
        binding,
        as_of=LATER,
    )
