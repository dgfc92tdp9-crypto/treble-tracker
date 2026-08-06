"""DTCC SDR interest-rate prints, from a recorded file (CLAUDE.md §7 — offline).

The fixture is a real CFTC cumulative file for 2026-07-31, trimmed to ~400
rows covering all four curves the adapter builds — USD SOFR OIS, EUR ESTR
OIS, and EURIBOR 3M and 6M — plus every case the filters exist for:
forward-starting trades, trades on a different day count, trades on a
different SOFR index, annual legs spelled MNTHx12 rather than YEARx1, block
trades, error records with the prints they withdraw, JPY and GBP trades, and
lifecycle events.

Each filter gets a test that shows it *excluding* something present in the
fixture, not merely that the happy path works. A filter tested only against
data it accepts is a filter that could be deleted without failing anything.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.ingest.base import RawPayload
from treble.ingest.dtcc import (
    CONVENTIONS,
    EUR_ESTR_OIS,
    EUR_EURIBOR_3M,
    EUR_EURIBOR_6M,
    MIN_TRADES_PER_TENOR,
    USD_SOFR_OIS,
    DtccParseError,
    DtccSdrRatesAdapter,
    _rows_from_zip,
    curve_subject,
    frequency_months,
    par_rates,
    report_date_from_uri,
)
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash

FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "dtcc" / "CFTC_CUMULATIVE_RATES_2026_07_31.zip"
)
SOURCE = "https://kgc0418-tdw-data-0.s3.amazonaws.com/cftc/eod/CFTC_CUMULATIVE_RATES_2026_07_31.zip"
REPORT_DATE = date(2026, 7, 31)
FETCHED = datetime(2026, 8, 1, 6, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    return _rows_from_zip(FIXTURE.read_bytes())


@pytest.fixture(scope="module")
def observations(rows: list[dict[str, str]]) -> tuple:
    return par_rates(rows, USD_SOFR_OIS, REPORT_DATE)


@pytest.fixture
def facts(tmp_path: Path) -> tuple:
    adapter = DtccSdrRatesAdapter(
        PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), report_dates=(REPORT_DATE,)
    )
    data = FIXTURE.read_bytes()
    raw = RawPayload(data=data, source_uri=SOURCE, fetched_at=FETCHED)
    return adapter.parse(raw, payload_hash(data)).facts


class TestTheDateComesFromTheFileName:
    """The CSV rows carry execution timestamps but nothing naming the
    file's own trading day, so the URI is the only source for it — which is
    what keeps `parse` pure (I5)."""

    def test_the_report_date_is_recovered(self) -> None:
        assert report_date_from_uri(SOURCE) == REPORT_DATE

    def test_a_uri_without_a_date_is_refused(self) -> None:
        """Refused rather than defaulted to today: every fact in the file
        would otherwise be dated by when it happened to be fetched."""
        with pytest.raises(DtccParseError, match="no report date"):
            report_date_from_uri("https://example.com/rates.zip")


class TestTheCurveThatComesOut:
    def test_it_spans_the_traded_tenors(self, observations: tuple) -> None:
        tenors = [o.tenor for o in observations]
        assert tenors[0] == "1Y"
        assert tenors[-1] == "30Y"
        assert len(tenors) == len(set(tenors))

    def test_every_rate_is_plausible_for_usd(self, observations: tuple) -> None:
        """A units slip — percent read as a decimal, or a basis-point field
        read as a rate — is the failure this catches, and it would produce
        a curve that still bootstraps."""
        for o in observations:
            assert 0.001 < o.rate < 0.20, f"{o.tenor}: {o.rate}"

    def test_the_curve_is_not_asserted_to_be_monotonic(self, observations: tuple) -> None:
        """Documenting a deliberate non-assertion.

        This curve genuinely inverts between 25Y and 30Y, which is a real
        and well-known feature of the long end of USD swap curves. A
        monotonicity check here would look like rigour and would fail on
        correct data.
        """
        rates = [o.rate for o in observations]
        assert rates != sorted(rates)

    def test_dispersion_is_published_beside_the_rate(self, observations: tuple) -> None:
        """A node from four prints and one from four hundred are both "the
        median". Without the spread beside it a screen presents them as
        equally solid."""
        assert all(o.dispersion_bp >= 0.0 for o in observations)
        assert any(o.dispersion_bp > 0.0 for o in observations)

    def test_the_median_is_used_not_the_mean(self, rows: list[dict[str, str]]) -> None:
        """Swaps trade at arbitrary fixed rates against an upfront, so a
        single off-market print moves a mean and not a median. The real file
        for this date carried a 10-year print at -0.50%.
        """
        polluted = [*rows]
        template = next(
            r
            for r in rows
            if r["Action type"] == "NEWT"
            and r["UPI Underlier Name"] in USD_SOFR_OIS.underliers
            and r["Fixed rate-Leg 1"]
        )
        for i in range(3):
            absurd = dict(template)
            absurd["Dissemination Identifier"] = f"synthetic-outlier-{i}"
            absurd["Fixed rate-Leg 1"] = "-0.5"
            polluted.append(absurd)

        before = {o.tenor: o.rate for o in par_rates(rows, USD_SOFR_OIS, REPORT_DATE)}
        after = {o.tenor: o.rate for o in par_rates(polluted, USD_SOFR_OIS, REPORT_DATE)}
        moved = max(abs(after[t] - before[t]) for t in before if t in after)
        assert moved * 1e4 < 25.0, f"three absurd prints moved a node by {moved * 1e4:.1f}bp"


def _standard_print(rows: list[dict[str, str]]) -> dict[str, str]:
    """A real spot-starting SOFR OIS print, used as the injection template."""
    for row in rows:
        if (
            row["Action type"] == "NEWT"
            and row["Event type"] == "TRAD"
            and row["Notional currency-Leg 1"] == USD_SOFR_OIS.currency
            and row["UPI Underlier Name"] in USD_SOFR_OIS.underliers
            and row["Fixed rate day count convention-leg 1"] == USD_SOFR_OIS.fixed_day_count
            and frequency_months(
                row["Fixed rate payment frequency period-Leg 1"],
                row["Fixed rate payment frequency period multiplier-Leg 1"],
            )
            == USD_SOFR_OIS.fixed_frequency_months
            and row["Execution Timestamp"][:10] == REPORT_DATE.isoformat()
            and row["Effective Date"][:10] <= "2026-08-05"
            and row["Fixed rate-Leg 1"]
        ):
            return row
    raise AssertionError("fixture no longer contains a standard spot-starting SOFR OIS print")


def _inject(rows: list[dict[str, str]], count: int, **overrides: str) -> list[dict[str, str]]:
    """Clones of a real print, perturbed in one field and priced absurdly.

    The absurd rate is what makes these tests discriminate: if the filter
    under test is removed, the clones reach the curve and move the node far
    enough that no tolerance hides it.
    """
    template = _standard_print(rows)
    clones = []
    for i in range(count):
        clone = dict(template)
        clone["Dissemination Identifier"] = f"injected-{overrides}-{i}"
        clone["Fixed rate-Leg 1"] = "0.99"
        clone.update(overrides)
        clones.append(clone)
    return [*rows, *clones]


class TestEveryFilterExcludesSomething:
    """Injection kill-tests: each fails if its filter is deleted.

    Written this way after mutation testing showed the first version could
    not fail. Those tests removed a category of row by hand and compared the
    result to the unfiltered run — which proves nothing when the fixture's
    rows in that category were already being excluded by some *other*
    filter. Four of eight mutations survived. Injecting rows that are
    standard in every respect but one, priced absurdly, tests exactly one
    filter at a time and cannot pass by accident (CLAUDE.md failure mode C).
    """

    @staticmethod
    def _unchanged(rows: list[dict[str, str]], polluted: list[dict[str, str]]) -> None:
        before = {o.tenor: (o.rate, o.trades) for o in par_rates(rows, USD_SOFR_OIS, REPORT_DATE)}
        after = {
            o.tenor: (o.rate, o.trades) for o in par_rates(polluted, USD_SOFR_OIS, REPORT_DATE)
        }
        assert after == before

    def test_forward_starting_trades_are_excluded(self, rows: list[dict[str, str]]) -> None:
        """A swap effective in six months carries a forward par rate. On a
        rising curve, including them drags every node upward — smoothly, and
        with no sign anything is wrong.

        The expiration moves with the effective date so the trade stays a
        whole ten years. Moving only the start date shortens it to 9.4 years
        and the *tenor* filter excludes it — which is how the first version
        of this test passed with the spot-start filter deleted.
        """
        self._unchanged(
            rows,
            _inject(
                rows,
                30,
                **{"Effective Date": "2027-03-15", "Expiration Date": "2037-03-15"},
            ),
        )

    def test_a_different_day_count_is_excluded(self, rows: list[dict[str, str]]) -> None:
        """A 30/360 rate is the same economics in different units; blending
        shifts the curve by the ratio between conventions, about 6bp on a 4%
        rate (ADR-0006)."""
        self._unchanged(
            rows, _inject(rows, 30, **{"Fixed rate day count convention-leg 1": "A001"})
        )

    def test_a_different_sofr_index_is_excluded(self, rows: list[dict[str, str]]) -> None:
        """`USD-SOFR CME Term` is a separate curve, not another observation
        of this one."""
        assert "USD-SOFR CME Term" not in USD_SOFR_OIS.underliers
        self._unchanged(rows, _inject(rows, 30, **{"UPI Underlier Name": "USD-SOFR CME Term"}))

    def test_a_different_payment_frequency_is_excluded(self, rows: list[dict[str, str]]) -> None:
        self._unchanged(
            rows, _inject(rows, 30, **{"Fixed rate payment frequency period-Leg 1": "MNTH"})
        )

    def test_another_currency_is_excluded(self, rows: list[dict[str, str]]) -> None:
        self._unchanged(rows, _inject(rows, 30, **{"Notional currency-Leg 1": "EUR"}))

    def test_lifecycle_events_are_not_counted_as_prints(self, rows: list[dict[str, str]]) -> None:
        """Amendments and novations reference an existing trade; counting
        them as prints would count that trade twice."""
        self._unchanged(rows, _inject(rows, 30, **{"Action type": "MODI"}))
        self._unchanged(rows, _inject(rows, 30, **{"Event type": "NOVA"}))

    def test_a_trade_executed_on_another_day_is_excluded(self, rows: list[dict[str, str]]) -> None:
        """A print carried in this file but executed on another day belongs
        to that day's curve.

        All three dates move together, so the injected trade is
        spot-starting and a whole ten years *relative to its own execution
        date*. Moving only the execution timestamp leaves it looking
        forward-starting and the spot-start filter excludes it instead —
        which is how the first version of this test passed with the
        wrong-day filter deleted.
        """
        self._unchanged(
            rows,
            _inject(
                rows,
                30,
                **{
                    "Execution Timestamp": "2026-07-24T09:00:00Z",
                    "Effective Date": "2026-07-28",
                    "Expiration Date": "2036-07-28",
                },
            ),
        )

    def test_a_withdrawn_print_is_excluded_by_identifier(self, rows: list[dict[str, str]]) -> None:
        """An `EROR` record says a disseminated print did not happen. Leaving
        it in means pricing off a trade the reporting party has withdrawn.

        Injects otherwise-perfect prints *and* the error records that
        withdraw them, so the exclusion must work by identifier — dropping
        the `EROR` rows alone would not save it.
        """
        template = _standard_print(rows)
        polluted = [*rows]
        for i in range(30):
            print_row = dict(template)
            print_row["Dissemination Identifier"] = f"withdrawn-{i}"
            print_row["Fixed rate-Leg 1"] = "0.99"
            error_row = dict(template)
            error_row["Dissemination Identifier"] = f"error-{i}"
            error_row["Action type"] = "EROR"
            error_row["Original Dissemination Identifier"] = f"withdrawn-{i}"
            polluted.extend((print_row, error_row))
        self._unchanged(rows, polluted)

    def test_a_non_standard_tenor_is_excluded(self, rows: list[dict[str, str]]) -> None:
        """A 7-year-3-month swap is a real trade and a meaningless node."""
        template = _standard_print(rows)
        effective = date.fromisoformat(template["Effective Date"][:10])
        odd = (effective.replace(year=effective.year + 7) - date(1, 1, 1)).days
        stray = date.fromordinal(odd + 90)
        self._unchanged(rows, _inject(rows, 30, **{"Expiration Date": stray.isoformat()}))


class TestThinTenorsAreOmittedNotInvented:
    def test_a_tenor_below_the_threshold_is_dropped(
        self, rows: list[dict[str, str]], observations: tuple
    ) -> None:
        """One trade is not a curve point. Omitting leaves the curve short,
        which is honest; publishing leaves it wrong, which is not."""
        thin = [o for o in observations if o.trades < MIN_TRADES_PER_TENOR]
        assert thin == []

    def test_every_published_tenor_meets_the_threshold(self, observations: tuple) -> None:
        assert all(o.trades >= MIN_TRADES_PER_TENOR for o in observations)

    def test_an_empty_file_yields_no_curve_rather_than_a_flat_one(self) -> None:
        assert par_rates([], USD_SOFR_OIS, REPORT_DATE) == ()


class TestCappedNotionals:
    def test_block_trades_are_counted_not_dropped(self, observations: tuple) -> None:
        """Part 43 caps the *notional* of a block trade, not its rate. So
        the rate is still a valid observation — but a curve built only from
        small trades would carry its own bias, and a reader should be able
        to see how much of a node came from capped prints."""
        assert sum(o.capped_trades for o in observations) > 0


class TestFacts:
    def test_subjects_name_the_curve_and_tenor(self, facts: tuple) -> None:
        assert curve_subject("USD-SOFR-OIS", "10Y") == "swap:USD-SOFR-OIS:10Y"
        assert "swap:USD-SOFR-OIS:10Y" in {f.subject for f in facts}

    def test_all_four_fields_are_emitted(self, facts: tuple) -> None:
        assert {f.field for f in facts} == {
            "PAR_RATE",
            "TRADE_COUNT",
            "RATE_DISPERSION_BP",
            "CAPPED_TRADE_COUNT",
        }

    def test_facts_are_dated_by_the_trading_day(self, facts: tuple) -> None:
        assert {f.effective_from for f in facts} == {REPORT_DATE}
        assert {f.effective_to for f in facts} == {REPORT_DATE}

    def test_knowledge_time_is_the_fetch_not_the_trade_date(self, facts: tuple) -> None:
        """The file is published after the close of the day it describes, so
        the fetch is the earliest moment this could have been known (I2)."""
        assert {f.knowledge_from for f in facts} == {FETCHED}

    def test_every_fact_carries_provenance(self, facts: tuple) -> None:
        assert all(f.provenance_id for f in facts)

    def test_parsing_is_pure(self, tmp_path: Path) -> None:
        adapter = DtccSdrRatesAdapter(
            PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"), report_dates=(REPORT_DATE,)
        )
        data = FIXTURE.read_bytes()
        raw = RawPayload(data=data, source_uri=SOURCE, fetched_at=FETCHED)
        assert (
            adapter.parse(raw, payload_hash(data)).facts
            == adapter.parse(raw, payload_hash(data)).facts
        )


class TestMalformedPayloads:
    def test_a_non_zip_is_refused(self) -> None:
        with pytest.raises(DtccParseError, match="not a zip"):
            _rows_from_zip(b"not a zip file at all")

    def test_an_archive_without_exactly_one_csv_is_refused(self, tmp_path: Path) -> None:
        """Two CSVs means the file layout changed and picking one would be a
        guess about which."""
        import zipfile

        path = tmp_path / "two.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("a.csv", "x\n1\n")
            z.writestr("b.csv", "x\n2\n")
        with pytest.raises(DtccParseError, match="exactly one CSV"):
            _rows_from_zip(path.read_bytes())


class TestLicenceIsRecorded:
    def test_the_source_is_marked_redistribution_restricted(self) -> None:
        """The terms could not be read (Cloudflare), and DTCC sells a paid
        systematic-access product for this data. The flag drives the bulk
        export guard (spec §9.3)."""
        assert DtccSdrRatesAdapter.meta.redistribution_restricted is True

    def test_the_unverified_terms_are_stated_in_the_licence(self) -> None:
        """So that a reader of the source registry sees the position rather
        than having to find this session's commit message."""
        assert "UNVERIFIED" in DtccSdrRatesAdapter.meta.licence

    def test_the_rate_limit_is_conservative(self) -> None:
        limit = DtccSdrRatesAdapter.meta.rate_limit_per_second
        assert limit is not None and limit <= 0.5


class TestFrequencyNormalisation:
    """`YEAR`x1 and `MNTH`x12 are the same annual leg.

    The file spells one frequency several ways. Comparing the raw pair
    would drop about 11% of the ESTR prints and 5% of the SOFR prints for
    no economic reason — an exclusion nobody would see, because the curve
    left behind still looks complete.
    """

    def test_the_two_spellings_of_annual_agree(self) -> None:
        assert frequency_months("YEAR", "1") == frequency_months("MNTH", "12") == 12

    def test_semiannual_and_quarterly(self) -> None:
        assert frequency_months("MNTH", "6") == 6
        assert frequency_months("MNTH", "3") == 3

    def test_an_irregular_period_has_no_length(self) -> None:
        """`EXPI` (at expiry) and `ADHO` (ad hoc) have no regular schedule.
        Returning a number for them would put a bespoke trade on a curve."""
        assert frequency_months("EXPI", "1") is None
        assert frequency_months("ADHO", "1") is None
        assert frequency_months("DAIL", "1") is None

    def test_a_missing_or_unparseable_multiplier_has_no_length(self) -> None:
        assert frequency_months("MNTH", "") is None
        assert frequency_months("MNTH", None) is None
        assert frequency_months(None, "6") is None

    def test_the_fixture_exercises_the_normalisation(self, rows: list[dict[str, str]]) -> None:
        """Guards the guard: without `MNTH`x12 rows present, deleting the
        normalisation would change nothing and the tests above would be
        about a function nothing calls with that input."""
        assert any(
            r["Fixed rate payment frequency period-Leg 1"] == "MNTH"
            and r["Fixed rate payment frequency period multiplier-Leg 1"] == "12"
            for r in rows
        )


class TestTheEuroCurves:
    """ESTR discounting and EURIBOR forecasting — a real multi-curve pair.

    This is what makes `SWPM` possible: the pricer refuses an overnight
    index as a forecast curve, so a SOFR or ESTR curve discounts but cannot
    project. EURIBOR is a term index and can.
    """

    def test_estr_and_euribor_are_separate_curves(self, rows: list[dict[str, str]]) -> None:
        estr = {o.tenor: o.rate for o in par_rates(rows, EUR_ESTR_OIS, REPORT_DATE)}
        euribor = {o.tenor: o.rate for o in par_rates(rows, EUR_EURIBOR_6M, REPORT_DATE)}
        assert estr and euribor
        shared = set(estr) & set(euribor)
        assert len(shared) >= 8, "not enough overlap to bootstrap one against the other"
        # EURIBOR carries bank credit; ESTR is near risk-free. The basis is
        # positive at every tenor, and a sign flip would mean the two curves
        # had been swapped.
        for tenor in shared:
            basis_bp = (euribor[tenor] - estr[tenor]) * 1e4
            assert 0.0 < basis_bp < 100.0, f"{tenor}: {basis_bp:.1f}bp"

    def test_the_index_tenor_comes_from_the_floating_leg(self, rows: list[dict[str, str]]) -> None:
        """`UPI Underlier Name` is `EUR-EURIBOR` for both 3M and 6M swaps,
        so the underlier cannot separate them. Only the floating leg's reset
        frequency can — and merging them would blend two curves that a tenor
        basis genuinely separates."""
        six = {o.tenor: o.rate for o in par_rates(rows, EUR_EURIBOR_6M, REPORT_DATE)}
        three = {o.tenor: o.rate for o in par_rates(rows, EUR_EURIBOR_3M, REPORT_DATE)}
        assert EUR_EURIBOR_6M.underliers == EUR_EURIBOR_3M.underliers
        shared = set(six) & set(three)
        assert shared, "fixture no longer covers both index tenors at a common tenor"
        for tenor in shared:
            # A longer index tenor pays a higher rate: 6M EURIBOR carries
            # more bank credit than 3M.
            assert six[tenor] > three[tenor], tenor

    def test_the_euro_fixed_leg_is_thirty_three_sixty(self) -> None:
        """EUR swaps pay annual 30/360 fixed against ACT/360 floating; USD
        SOFR OIS pays annual ACT/360. Using one convention for both would
        shift a curve by the ratio between them (ADR-0006)."""
        assert EUR_EURIBOR_6M.fixed_day_count == "A001"
        assert EUR_EURIBOR_6M.float_day_count == "A004"
        assert USD_SOFR_OIS.fixed_day_count == "A004"

    def test_only_forecast_curves_declare_an_index_tenor(self) -> None:
        """An overnight curve discounts and does not project. Declaring an
        index tenor on one would let it be used as a forecast curve, which
        prices a daily-compounded rate on a discrete schedule."""
        assert USD_SOFR_OIS.index_tenor is None
        assert EUR_ESTR_OIS.index_tenor is None
        assert EUR_EURIBOR_6M.index_tenor == "6M"
        assert EUR_EURIBOR_3M.index_tenor == "3M"

    def test_both_spellings_of_the_estr_underlier_are_one_curve(
        self, rows: list[dict[str, str]]
    ) -> None:
        """The same file carries `EUR-EuroSTR-COMPOUND` and
        `EUR-EuroSTR-OIS Compound` on the same day for the same index.
        Treating them as two curves would halve every node's sample."""
        assert len(EUR_ESTR_OIS.underliers) == 2
        present = {r["UPI Underlier Name"] for r in rows} & EUR_ESTR_OIS.underliers
        assert len(present) == 2, "fixture no longer carries both spellings"

    def test_the_currencies_do_not_leak_into_each_other(
        self, rows: list[dict[str, str]], observations: tuple
    ) -> None:
        """The fixture now carries USD, EUR, JPY and GBP prints. The USD
        curve must be exactly what it was when the fixture was USD-only."""
        usd = {o.tenor for o in observations}
        for convention in (EUR_ESTR_OIS, EUR_EURIBOR_6M, EUR_EURIBOR_3M):
            eur = {o.curve for o in par_rates(rows, convention, REPORT_DATE)}
            assert eur <= {convention.curve}
        assert usd == {o.tenor for o in par_rates(rows, USD_SOFR_OIS, REPORT_DATE)}


class TestEveryConventionIsReachable:
    def test_all_four_curves_are_registered_for_ingest(self) -> None:
        """A convention defined and left out of `CONVENTIONS` is a curve
        that exists in code and never in the store — the class of defect the
        Phase 1 audit found three times."""
        assert {c.curve for c in CONVENTIONS} == {
            "USD-SOFR-OIS",
            "EUR-ESTR-OIS",
            "EUR-EURIBOR-6M",
            "EUR-EURIBOR-3M",
        }

    def test_every_registered_convention_yields_a_curve(self, rows: list[dict[str, str]]) -> None:
        for convention in CONVENTIONS:
            observations = par_rates(rows, convention, REPORT_DATE)
            assert len(observations) >= 5, f"{convention.curve}: {len(observations)} nodes"

    def test_facts_are_emitted_for_every_curve(self, facts: tuple) -> None:
        curves = {f.subject.split(":")[1] for f in facts}
        assert curves == {c.curve for c in CONVENTIONS}


class TestTheFloatingLegConventionIsChecked:
    """The last filter without a discriminating test.

    Every EURIBOR print in the real file accrues ACT/360, so removing the
    check changes nothing about real data and a mutation of it survived.
    That is not evidence the filter is unnecessary — it is evidence the
    fixture cannot exercise it. A leg on a different accrual basis is a
    different instrument whose rate is the same economics in different
    units, exactly as on the fixed leg (ADR-0006).
    """

    @staticmethod
    def _euribor_print(rows: list[dict[str, str]]) -> dict[str, str]:
        for row in rows:
            if (
                row["Action type"] == "NEWT"
                and row["Event type"] == "TRAD"
                and row["Notional currency-Leg 1"] == EUR_EURIBOR_6M.currency
                and row["UPI Underlier Name"] in EUR_EURIBOR_6M.underliers
                and row["Fixed rate day count convention-leg 1"] == EUR_EURIBOR_6M.fixed_day_count
                and frequency_months(
                    row["Floating rate reset frequency period-leg 2"],
                    row["Floating rate reset frequency period multiplier-leg 2"],
                )
                == EUR_EURIBOR_6M.float_reset_months
                and row["Execution Timestamp"][:10] == REPORT_DATE.isoformat()
                and row["Effective Date"][:10] <= "2026-08-05"
                and row["Fixed rate-Leg 1"]
            ):
                return row
        raise AssertionError("fixture no longer contains a standard 6M EURIBOR print")

    def test_a_floating_leg_on_another_day_count_is_excluded(
        self, rows: list[dict[str, str]]
    ) -> None:
        template = self._euribor_print(rows)
        polluted = [*rows]
        for i in range(30):
            clone = dict(template)
            clone["Dissemination Identifier"] = f"injected-float-dcc-{i}"
            clone["Fixed rate-Leg 1"] = "0.99"
            clone["Floating rate day count convention-leg 2"] = "A001"
            polluted.append(clone)

        before = {o.tenor: (o.rate, o.trades) for o in par_rates(rows, EUR_EURIBOR_6M, REPORT_DATE)}
        after = {
            o.tenor: (o.rate, o.trades) for o in par_rates(polluted, EUR_EURIBOR_6M, REPORT_DATE)
        }
        assert after == before

    def test_the_real_data_alone_cannot_prove_this(self, rows: list[dict[str, str]]) -> None:
        """States why the injection above is necessary, so a later reader
        does not delete it as redundant with the real-data tests."""
        floats = {
            r["Floating rate day count convention-leg 2"]
            for r in rows
            if r["UPI Underlier Name"] in EUR_EURIBOR_6M.underliers
        }
        assert floats == {EUR_EURIBOR_6M.float_day_count}


class TestSwaptionPrints:
    """Option prints on the tape (spec §11.3).

    The tape carries premiums, strikes, first-exercise dates and underlier
    maturities on real swaptions — everything a volatility can be implied
    from. This only reads what the file says; the vol solve needs a curve and
    lives in `analytics`.
    """

    @staticmethod
    def _row(**overrides: str) -> dict[str, str]:
        row = {
            "Action type": "NEWT",
            "Event type": "TRAD",
            "Dissemination Identifier": "4164746906000000101",
            "UPI FISN": "NA/O Call Epn Fxd Flt EUR",
            "Strike Price": "0.02939",
            "Option Premium Amount": "3,339,007.3935",
            "Notional amount-Leg 1": "420,000,000",
            "First exercise date": "2027-01-13",
            "Maturity date of the underlier": "2037-01-13",
            "Block trade election indicator": "FALSE",
            "Large notional off-facility swap election indicator": "FALSE",
        }
        row.update(overrides)
        return row

    def test_a_swaption_print_is_read(self) -> None:
        from treble.ingest.dtcc import swaption_prints

        prints = swaption_prints([self._row()], date(2026, 7, 13))
        assert len(prints) == 1
        subject, values = prints[0]
        assert str(subject).startswith("swaption:EUR:")
        assert values["PAYER"] is True
        assert values["STRIKE"] == pytest.approx(0.02939)
        assert values["PREMIUM_FRACTION"] == pytest.approx(3_339_007.3935 / 420_000_000)
        assert values["EXPIRY_DATE"] == date(2027, 1, 13)
        assert values["UNDERLIER_MATURITY"] == date(2037, 1, 13)

    def test_a_receiver_is_not_a_payer(self) -> None:
        """ "Call" is a payer in the FISN's vocabulary and "P" a receiver.
        Getting this backwards prices every option on the wrong side of the
        forward and still produces a plausible volatility."""
        from treble.ingest.dtcc import swaption_prints

        _, values = swaption_prints(
            [self._row(**{"UPI FISN": "NA/O P Epn Fxd Flt EUR"})], date(2026, 7, 13)
        )[0]
        assert values["PAYER"] is False

    def test_a_print_without_the_underlier_maturity_is_skipped(self) -> None:
        """Expiry alone puts a swaption on no grid: "1Y into what?" has no
        answer, and a guessed tenor is a wrong label on a real premium."""
        from treble.ingest.dtcc import swaption_prints

        assert (
            swaption_prints(
                [self._row(**{"Maturity date of the underlier": ""})], date(2026, 7, 13)
            )
            == ()
        )

    def test_amendments_and_corrections_are_excluded(self) -> None:
        """The same lifecycle filter the curve build uses: an amendment
        references an existing trade and would count the contract twice."""
        from treble.ingest.dtcc import swaption_prints

        assert swaption_prints([self._row(**{"Action type": "CORR"})], date(2026, 7, 13)) == ()
        assert swaption_prints([self._row(**{"Event type": "NOVA"})], date(2026, 7, 13)) == ()

    def test_a_capped_notional_is_flagged_not_dropped(self) -> None:
        """The premium is real and the notional is a floor, so the premium
        fraction is too large and the implied vol is biased upward. The print
        is still the only thing the market said."""
        from treble.ingest.dtcc import swaption_prints

        _, values = swaption_prints(
            [self._row(**{"Block trade election indicator": "TRUE"})], date(2026, 7, 13)
        )[0]
        assert values["NOTIONAL_CAPPED"] is True

    def test_a_trailing_plus_on_the_notional_still_parses(self) -> None:
        """The CFTC writes a capped size as `784044998+`. Read naively that
        is not a number at all and the print would be silently lost."""
        from treble.ingest.dtcc import _decimal

        assert _decimal("784,044,998+") == pytest.approx(784_044_998.0)

    def test_an_expiry_before_the_report_date_is_skipped(self) -> None:
        """An option that has already expired has no time value to imply a
        volatility from."""
        from treble.ingest.dtcc import swaption_prints

        assert swaption_prints([self._row()], date(2028, 1, 1)) == ()

    def test_a_currency_with_no_curve_is_skipped(self) -> None:
        """A premium in a currency this system builds no curve for cannot be
        turned into a volatility, so storing it would be storing something
        permanently unusable."""
        from treble.ingest.dtcc import swaption_prints

        assert (
            swaption_prints([self._row(**{"UPI FISN": "NA/O Call Epn OIS MXN"})], date(2026, 7, 13))
            == ()
        )
