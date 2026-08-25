"""One instrument, several holders (spec §8.3).

The defect these cover, measured on the live store on 2026-08-25: three funds
reported `isin:US7185461040` for 2026-03-31 at $1,865,427,008.96, $35,011,534.58
and $4,390,538.00. All three were stored. Every screen showed $4,390,538 —
the smallest — because the visibility window partitions on subject and field
and returns the row with the latest `knowledge_from`, and that fund's filing
happened to be fetched last.

It never appeared in `DuckStore.ambiguous_partitions` either, which is the
part worth remembering: that check also groups by `knowledge_from`, so it
only sees values colliding at the *same* knowledge time. Filings fetched
seconds apart collide in the window and not in the check. A partition can
lose data without ever being ambiguous, so "no ambiguous partitions" was
never evidence that nothing was hidden.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.storebuilder import DAY, LATER, StoreBuilder, _isin
from treble.core.identifiers import parse_position_subject, position_subject
from treble.tapi.issuer_curves import bond_rows
from treble.tapi.positions import SUMMED_FIELDS, totals_by_instrument, totals_for


@pytest.fixture
def two_holders(tmp_path: Path) -> StoreBuilder:
    """The same 60 bonds held by two funds, the second at a quarter the size."""
    return StoreBuilder(tmp_path / "t.db").with_bonds().with_second_holder()


class TestBothHoldersAreCounted:
    def test_the_total_is_the_sum_not_one_filing(self, two_holders: StoreBuilder) -> None:
        instrument = f"isin:{_isin(0)}"
        totals = totals_for(two_holders.store, instrument, as_of=LATER)
        # 1,000,000 par from the first fund and 250,000 from the second.
        assert totals["nport:balance"] == pytest.approx(1_250_000.0)

    def test_the_total_exceeds_either_holding(self, two_holders: StoreBuilder) -> None:
        """A sum is not a choice. Returning either fund's number alone would
        satisfy a weaker assertion and is exactly what went wrong."""
        instrument = f"isin:{_isin(0)}"
        total = totals_for(two_holders.store, instrument, as_of=LATER)["nport:valUSD"]
        each = [
            fact.value
            for subject in two_holders.store.subjects_with_prefix(f"pos:{instrument}:", as_of=LATER)
            for fact in two_holders.store.subject_facts(subject, as_of=LATER)
            if fact.field == "nport:valUSD"
        ]
        assert len(each) == 2, each
        assert total > max(each)
        assert total == pytest.approx(sum(each))

    def test_bond_rows_carries_the_summed_holding(self, two_holders: StoreBuilder) -> None:
        rows = {
            (r["identifier"], r["report_date"]): r
            for r in bond_rows(two_holders.store, as_of=LATER)
        }
        row = rows[(f"isin:{_isin(0)}", DAY)]
        assert row["nport:balance"] == pytest.approx(1_250_000.0)
        # The instrument's own facts still come from the instrument.
        assert row["nport:assetCat"] == "DBT"
        assert row["nport:issuerCat"] == "CORP"

    def test_one_holder_is_unchanged(self, tmp_path: Path) -> None:
        """The ordinary case must not have been made stranger by the split."""
        single = StoreBuilder(tmp_path / "s.db").with_bonds()
        totals = totals_for(single.store, f"isin:{_isin(0)}", as_of=LATER)
        assert totals["nport:balance"] == pytest.approx(1_000_000.0)


class TestOnlyAdditiveFieldsAreSummed:
    def test_pct_of_portfolio_is_not_summed(self) -> None:
        """Two funds each holding 3% of their own book do not hold 6% of
        anything. Adding percentages produces a number that is not a
        percentage, so `pctVal` is deliberately not summable."""
        assert "nport:pctVal" not in SUMMED_FIELDS
        assert set(SUMMED_FIELDS) == {"nport:valUSD", "nport:balance"}


class TestThePositionKey:
    def test_it_round_trips(self) -> None:
        subject = position_subject(fund="S000052180", instrument="isin:US7185461040")
        assert parse_position_subject(subject) == ("S000052180", "isin:US7185461040")

    def test_the_instrument_leads_so_one_bond_can_be_prefix_matched(self) -> None:
        """Readers ask what is held in a bond, never what a fund holds. Fund
        first would force a sweep of every position in the store per bond."""
        subject = position_subject(fund="S000052180", instrument="isin:US7185461040")
        assert str(subject).startswith("pos:isin:US7185461040:")

    def test_a_colon_in_the_fund_cannot_forge_a_segment(self) -> None:
        subject = position_subject(fund="odd:fund", instrument="isin:US7185461040")
        assert parse_position_subject(subject) == ("ODD-FUND", "isin:US7185461040")

    def test_an_unnamed_fund_is_refused(self) -> None:
        """Falling back to the bare instrument would put the position back on
        the key it is being moved off."""
        for fund in ("", "   "):
            with pytest.raises(ValueError, match="must name the fund"):
                position_subject(fund=fund, instrument="isin:US7185461040")

    def test_a_non_position_subject_parses_to_none(self) -> None:
        for subject in ("isin:US7185461040", "pos:", "pos:nocolon", "otc:X:swapDeriv"):
            assert parse_position_subject(subject) is None


class TestTotalsByInstrument:
    def test_it_keys_by_instrument_and_date(self, two_holders: StoreBuilder) -> None:
        totals = totals_by_instrument(two_holders.store, as_of=LATER)
        assert (f"isin:{_isin(0)}", DAY) in totals
        assert totals[(f"isin:{_isin(0)}", DAY)]["nport:valUSD"] > 0.0

    def test_an_empty_store_has_no_positions(self, tmp_path: Path) -> None:
        empty = StoreBuilder(tmp_path / "e.db")
        assert totals_by_instrument(empty.store, as_of=LATER) == {}
        assert totals_for(empty.store, "isin:US7185461040", as_of=LATER) == {}
