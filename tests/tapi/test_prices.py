"""Which subject a price comes from, and what kind of price it is.

The two things `GP` and `HP` had no way to express, and the reason they
were Index-only. Both are places where being *nearly* right is worse than
refusing: a price read off the wrong subject is a number for a different
instrument, and a total return labelled as a price is a number that means
something else.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.prices import (
    NoPriceSeriesError,
    listing_subject,
    price_basis,
    price_series,
    price_subject,
)

NOW = datetime(2026, 8, 23, tzinfo=UTC)
KNOWN = datetime(2026, 8, 1, tzinfo=UTC)


def _store(tmp_path: Path, rows: list[tuple[str, str, date, float]]) -> DuckStore:
    store = DuckStore(tmp_path / "t.db")
    record = Provenance(
        source_system="twelvedata",
        source_uri="https://example.invalid/x",
        retrieved_at=KNOWN,
        method=ExtractionMethod.API,
        extractor_version="1",
        payload_hash="a" * 64,
    )
    store.write_provenance([record])
    store.write_facts(
        [
            Fact(
                subject=TUID(subject),
                field=field,
                value=value,
                effective_from=day,
                effective_to=None,
                knowledge_from=KNOWN,
                provenance_id=record.id,
            )
            for subject, field, day, value in rows
        ]
    )
    return store


class TestTheListingIsNotTheCompany:
    def test_an_equity_reads_its_listing_not_its_filer(self, tmp_path: Path) -> None:
        """`AAPL US Equity` resolves to a CIK because that is where the
        fundamentals are. The price is written under `equity:AAPL`, and
        reading the CIK for a price finds nothing at all."""
        store = _store(
            tmp_path,
            [
                ("equity:AAPL", "ADJ_CLOSE", date(2026, 8, 20), 300.0),
                ("cik:0000320193", "us-gaap:Assets:USD", date(2026, 6, 30), 3.6e11),
            ],
        )
        assert price_subject(store, ticker="AAPL", resolve=lambda: TUID("cik:0000320193")) == TUID(
            "equity:AAPL"
        )

    def test_a_subject_with_no_listing_keeps_what_was_resolved(self, tmp_path: Path) -> None:
        """An index has no separate listing, so the fallback is what makes
        the Index namespace keep working unchanged."""
        store = _store(tmp_path, [("fred:SP500", "PX_LAST", date(2026, 8, 20), 7600.0)])
        assert price_subject(store, ticker="SP500", resolve=lambda: TUID("fred:SP500")) == TUID(
            "fred:SP500"
        )

    def test_a_ticker_with_no_stored_listing_does_not_invent_one(self, tmp_path: Path) -> None:
        """IBM is a filer in this store and not one of the 45 tickers with
        price history. Returning `equity:IBM` regardless would produce a
        subject with no facts, and a screen of dashes indistinguishable
        from a company nobody has prices for."""
        store = _store(tmp_path, [("cik:0000051143", "us-gaap:Assets:USD", date(2026, 6, 30), 1.0)])
        assert price_subject(store, ticker="IBM", resolve=lambda: TUID("cik:0000051143")) == TUID(
            "cik:0000051143"
        )

    def test_the_listing_key_is_the_upper_cased_ticker(self) -> None:
        assert listing_subject("aapl") == TUID("equity:AAPL")

    def test_a_listing_is_served_even_when_the_company_cannot_be_resolved(
        self, tmp_path: Path
    ) -> None:
        """A price does not need a filer, and requiring one refused a
        security whose prices were already in the store.

        BRK.B is the real case: EDGAR's company index spells the class B
        share `BRK-B` and Twelve Data spells it `BRK.B`, so resolving the
        company first raised for a ticker with 20,000 price facts under
        `equity:BRK.B`. Two vendors disagreeing about punctuation in a
        share-class suffix is normal and must not cost the chart.
        """
        store = _store(tmp_path, [("equity:BRK.B", "ADJ_CLOSE", date(2026, 8, 20), 700.0)])

        def unresolvable() -> TUID:
            raise AssertionError("the company was resolved before the listing was tried")

        assert price_subject(store, ticker="BRK.B", resolve=unresolvable) == TUID("equity:BRK.B")

    def test_the_company_is_still_resolved_when_there_is_no_listing(self, tmp_path: Path) -> None:
        """The fallback must stay lazy, not absent."""
        store = _store(tmp_path, [("fred:SP500", "PX_LAST", date(2026, 8, 20), 7600.0)])
        assert price_subject(store, ticker="SP500", resolve=lambda: TUID("fred:SP500")) == TUID(
            "fred:SP500"
        )


class TestTheSeriesSaysWhatItIs:
    def test_an_adjusted_series_is_reported_as_a_total_return(self, tmp_path: Path) -> None:
        store = _store(tmp_path, [("equity:AAPL", "ADJ_CLOSE", date(2026, 8, 20), 300.0)])
        series = price_series(store, TUID("equity:AAPL"), as_of=NOW)
        assert series.field == "ADJ_CLOSE"
        assert "total return" in series.basis

    def test_an_index_level_is_not_reported_as_adjusted(self, tmp_path: Path) -> None:
        store = _store(tmp_path, [("fred:SP500", "PX_LAST", date(2026, 8, 20), 7600.0)])
        series = price_series(store, TUID("fred:SP500"), as_of=NOW)
        assert series.field == "PX_LAST"
        assert "not adjusted" in series.basis

    def test_the_field_is_never_silently_renamed(self, tmp_path: Path) -> None:
        """The whole point. A column called PX_LAST that is silently a
        total return is what the ingest adapter refused to write, and a
        screen that relabelled it here would undo that."""
        store = _store(tmp_path, [("equity:AAPL", "ADJ_CLOSE", date(2026, 8, 20), 300.0)])
        assert price_series(store, TUID("equity:AAPL"), as_of=NOW).field != "PX_LAST"

    def test_a_subject_with_neither_field_refuses_and_names_the_subject(
        self, tmp_path: Path
    ) -> None:
        store = _store(tmp_path, [("cik:0000051143", "us-gaap:Assets:USD", date(2026, 6, 30), 1.0)])
        with pytest.raises(NoPriceSeriesError, match="cik:0000051143"):
            price_series(store, TUID("cik:0000051143"), as_of=NOW)

    def test_a_subject_holding_both_reports_the_adjusted_one(self, tmp_path: Path) -> None:
        """Order of `PRICE_FIELDS` is load-bearing. Calling a total return
        a last price misleads; calling a last price a total return merely
        over-warns, so the conservative label wins a tie."""
        store = _store(
            tmp_path,
            [
                ("equity:X", "PX_LAST", date(2026, 8, 20), 100.0),
                ("equity:X", "ADJ_CLOSE", date(2026, 8, 20), 95.0),
            ],
        )
        assert price_series(store, TUID("equity:X"), as_of=NOW).field == "ADJ_CLOSE"


class TestTheSeriesItself:
    @staticmethod
    def _five(tmp_path: Path) -> DuckStore:
        return _store(
            tmp_path,
            [("equity:A", "ADJ_CLOSE", date(2026, 8, 17 + n), 100.0 + n) for n in range(5)],
        )

    def test_points_are_in_date_order(self, tmp_path: Path) -> None:
        points = price_series(self._five(tmp_path), TUID("equity:A"), as_of=NOW).points
        assert [d for d, _ in points] == sorted(d for d, _ in points)

    def test_the_sort_holds_when_the_store_does_not_provide_one(self, tmp_path: Path) -> None:
        """`DuckStore.history` already orders by effective date, so against
        it the sort inside `price_series` is unobservable — deleting it
        passed every other test here.

        It is kept because `Store` is a protocol and the ordering is that
        one implementation's behaviour, not the interface's promise. `last`
        reads `points[-1]`, so an unordered implementation would report
        some arbitrary day's close as the current price, which is the exact
        class of silent wrongness this module exists to avoid. This feeds
        deliberately unsorted history through the same call.
        """

        class Shuffled:
            def __init__(self, inner: DuckStore) -> None:
                self._inner = inner

            def history(self, subject: TUID, field: str, **kwargs: object) -> list[Fact]:
                facts = self._inner.history(subject, field, **kwargs)  # type: ignore[arg-type]
                return list(reversed(facts))

            def __getattr__(self, name: str) -> object:
                return getattr(self._inner, name)

        store = Shuffled(self._five(tmp_path))
        series = price_series(store, TUID("equity:A"), as_of=NOW)  # type: ignore[arg-type]
        assert [d for d, _ in series.points] == sorted(d for d, _ in series.points)
        assert series.last_date == date(2026, 8, 21), "last read off an unordered store"
        assert series.last == 104.0

    def test_last_is_the_most_recent_not_the_largest(self, tmp_path: Path) -> None:
        store = _store(
            tmp_path,
            [
                ("equity:A", "ADJ_CLOSE", date(2026, 8, 17), 500.0),
                ("equity:A", "ADJ_CLOSE", date(2026, 8, 20), 100.0),
            ],
        )
        series = price_series(store, TUID("equity:A"), as_of=NOW)
        assert series.last == 100.0
        assert series.last_date == date(2026, 8, 20)

    def test_a_limit_keeps_the_recent_end(self, tmp_path: Path) -> None:
        """Truncating from the wrong end shows 2006 and calls it current —
        which on a 5,010-point equity series is most of the chart."""
        series = price_series(self._five(tmp_path), TUID("equity:A"), as_of=NOW, limit=2)
        assert [d for d, _ in series.points] == [date(2026, 8, 20), date(2026, 8, 21)]

    def test_a_point_in_time_read_hides_later_knowledge(self, tmp_path: Path) -> None:
        """`as_of` is honoured like every other read (I2), so a chart drawn
        as of a past date does not contain prices learned after it."""
        store = self._five(tmp_path)
        with pytest.raises(NoPriceSeriesError):
            price_series(store, TUID("equity:A"), as_of=datetime(2026, 7, 1, tzinfo=UTC))


class TestTheBasisPanel:
    def test_the_series_and_its_basis_come_first(self, tmp_path: Path) -> None:
        """GP shows a four-row window, so anything below the fourth row is
        invisible there. The ordering is what puts the label on screen."""
        store = _store(tmp_path, [("equity:A", "ADJ_CLOSE", date(2026, 8, 20), 300.0)])
        rows = price_basis(price_series(store, TUID("equity:A"), as_of=NOW))
        assert [label for label, _ in rows[:4]] == ["Series", "Basis", "Last", "As of"]

    def test_every_row_is_a_pair_of_strings(self, tmp_path: Path) -> None:
        store = _store(tmp_path, [("equity:A", "ADJ_CLOSE", date(2026, 8, 20), 300.0)])
        rows = price_basis(price_series(store, TUID("equity:A"), as_of=NOW))
        assert all(len(r) == 2 and all(isinstance(v, str) for v in r) for r in rows)

    def test_it_names_the_subject_it_read(self, tmp_path: Path) -> None:
        """So a reader can tell a price came from the listing rather than
        the filer without having to know the resolution rules."""
        store = _store(tmp_path, [("equity:A", "ADJ_CLOSE", date(2026, 8, 20), 300.0)])
        rows = dict(price_basis(price_series(store, TUID("equity:A"), as_of=NOW)))
        assert rows["Subject"] == "equity:A"
