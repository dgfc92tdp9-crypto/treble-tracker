"""The binding layer, on a populated store (spec §8.3).

`tapi/local.py` is the least-covered module in the repository, and the
reason is uniform: every thin method needs a store holding curves or
holdings, and building that by hand is most of the work. `StoreBuilder` is
that cost paid once.

These are the methods where a wrong number reaches a person, so what is
checked is that the row a screen shows is the number the service computed
— not that the service is correct, which its own suite covers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.storebuilder import LATER, StoreBuilder
from treble.tapi.local import LocalTapi


@pytest.fixture
def populated(tmp_path: Path) -> LocalTapi:
    builder = StoreBuilder(tmp_path / "t.db").with_curves(usd=True).with_bonds()
    return LocalTapi(builder.store)


class TestTheResidualPanel:
    """Its populated path was verified once by running it against the live
    store and never pinned. A change that broke the fit would have gone
    unnoticed."""

    def test_it_reports_a_verdict_on_real_curves(self, populated: LocalTapi) -> None:
        rows = dict(populated.series(None, "sys:tval_residual", as_of=LATER))  # type: ignore[arg-type]
        assert "BONDS MEASURED" in rows
        assert int(rows["BONDS MEASURED"]) > 0
        assert str(rows["VERDICT"]) in {
            "applied",
            "measured and rejected — curve stands alone",
        }

    def test_the_null_and_model_errors_are_both_reported(self, populated: LocalTapi) -> None:
        """Skill alone cannot say whether a 3% improvement was on 2bp of
        error or 200bp, and only one of those is worth acting on."""
        rows = dict(populated.series(None, "sys:tval_residual", as_of=LATER))  # type: ignore[arg-type]
        assert float(rows["NULL MAE (bp)"]) > 0.0
        assert float(rows["MODEL MAE (bp)"]) > 0.0

    def test_a_store_with_no_curves_says_so(self, tmp_path: Path) -> None:
        empty = LocalTapi(StoreBuilder(tmp_path / "e.db").store)
        rows = empty.series(None, "sys:tval_residual", as_of=LATER)
        assert rows[0][1] is None


class TestTheProductCatalogue:
    def test_it_lists_every_product_with_a_status(self, populated: LocalTapi) -> None:
        rows = populated.series(None, "sys:swpm_products", as_of=LATER)
        assert len(rows) == 7
        assert all(str(r[2]) for r in rows)


class TestTheSwpmPanels:
    """The four curve-driven panels, and the largest coverage gap in the
    binding layer: 53 statements across three of them. All they needed was
    a store with curves in it, which is why they went untested."""

    @pytest.mark.parametrize(
        "binding",
        ["sys:swpm_valuation", "sys:swpm_cashflows", "sys:swpm_ois", "sys:swpm_basis"],
    )
    def test_each_panel_returns_rows_on_a_real_curve(
        self, populated: LocalTapi, binding: str
    ) -> None:
        rows = populated.series(None, binding, as_of=LATER)
        assert rows, f"{binding} returned nothing on a populated store"
        assert all(isinstance(r, tuple) for r in rows)

    @pytest.mark.parametrize(
        "binding",
        ["sys:swpm_valuation", "sys:swpm_cashflows", "sys:swpm_ois", "sys:swpm_basis"],
    )
    def test_each_panel_says_why_on_an_empty_store(self, tmp_path: Path, binding: str) -> None:
        """A panel that renders blank and one whose inputs are absent look
        identical, and only the second is a data problem the reader can
        act on."""
        empty = LocalTapi(StoreBuilder(tmp_path / f"e-{binding[-6:]}.db").store)
        rows = empty.series(None, binding, as_of=LATER)
        assert rows
        assert any(isinstance(cell, str) and cell for cell in rows[0])

    def test_the_basis_panel_needs_two_real_curves(self, populated: LocalTapi) -> None:
        """A tenor basis is the difference between two curves the market
        quoted. Interpolating the shorter one from the longer would make
        the basis a function of the interpolator."""
        rows = populated.series(None, "sys:swpm_basis", as_of=LATER)
        assert rows


class TestTheTvalPanels:
    """The three issuer-curve panels. Bonds are all they need, and the
    builder already supplies them."""

    @pytest.mark.parametrize("binding", ["sys:tval_curves", "sys:tval_values", "sys:tval_method"])
    def test_each_panel_returns_rows_on_fitted_curves(
        self, populated: LocalTapi, binding: str
    ) -> None:
        rows = populated.series(None, binding, as_of=LATER)
        assert rows, f"{binding} returned nothing against 60 bonds across 4 issuers"

    def test_the_method_panel_states_what_it_assumed(self, populated: LocalTapi) -> None:
        """§15's drill-down is the honest centre of TVAL: N-PORT supplies no
        day count and no frequency, so the panel names the ones this model
        assumed rather than letting a reader infer they were observed."""
        text = " ".join(
            str(cell)
            for row in populated.series(None, "sys:tval_method", as_of=LATER)
            for cell in row
        )
        assert "assum" in text.lower() or "implied" in text.lower()

    @pytest.mark.parametrize("binding", ["sys:tval_curves", "sys:tval_values", "sys:tval_method"])
    def test_a_store_with_no_bonds_says_why(self, tmp_path: Path, binding: str) -> None:
        """A failure to fit returns the reason as a row rather than an empty
        table: a bond with nothing to say about it and a model that could
        not be built must not look the same."""
        empty = LocalTapi(StoreBuilder(tmp_path / f"t-{binding[-6:]}.db").store)
        rows = empty.series(None, binding, as_of=LATER)
        assert rows
        assert any(isinstance(cell, str) and cell for cell in rows[0])


@pytest.fixture
def with_returns(tmp_path: Path) -> LocalTapi:
    return LocalTapi(StoreBuilder(tmp_path / "f.db").with_factors().store)


class TestThePortPanels:
    """PORT's three panels are one factor fit read three ways. The fit is
    the expensive part and it happens once, so a panel that quietly stopped
    consulting it would still render — with numbers from nowhere."""

    def test_the_summary_states_the_window_it_fitted_over(self, with_returns: LocalTapi) -> None:
        """A factor summary without its window is uninterpretable: 250 days
        and 5 years give different betas and the screen must say which.

        Both ends are pinned. A window that reported only its start would
        pass while silently fitting to half the data."""
        rows = dict(with_returns.series(None, "sys:port_summary", as_of=LATER))  # type: ignore[arg-type]
        assert rows["Window"] == "2025-09-01 to 2026-05-08"
        assert int(rows["Observations"]) == 250

    def test_every_factor_is_named_with_its_own_return(self, with_returns: LocalTapi) -> None:
        """Six factors, six distinct annualised returns. A panel that
        reported one number six times would be a broken join, and a test
        that only counted rows could not tell the difference."""
        rows = with_returns.series(None, "sys:port_factors", as_of=LATER)
        names = [r[0] for r in rows]
        assert names == ["MKT_RF", "SMB", "HML", "RMW", "CMA", "MOM"]
        # Whole rows, not the return column alone: annualised returns are
        # rounded to two places and two factors can genuinely round to the
        # same figure, which failed this assertion for an uninteresting
        # reason.
        assert len({tuple(r[1:]) for r in rows}) == len(rows)

    def test_exposures_recover_the_betas_the_fixture_was_built_from(
        self, with_returns: LocalTapi
    ) -> None:
        """The fixture builds ASSET0..4 with market betas 0.6, 0.9, 1.2,
        1.5, 1.8. Recovering them is the only assertion here that could
        have failed for an interesting reason — row counts and column
        widths cannot tell a fitted exposure from a transposed one, and
        the first version of the fixture returned 0.91 for the 0.6 asset
        without anything going red.
        """
        rows = with_returns.series(None, "sys:port_exposures", as_of=LATER)
        betas = {str(r[0]): float(r[1]) for r in rows}  # type: ignore[arg-type]
        assert [*betas] == [f"ASSET{i}" for i in range(5)]
        for i in range(5):
            assert betas[f"ASSET{i}"] == pytest.approx(0.6 + 0.3 * i, abs=0.05)

    def test_an_empty_store_gives_a_reason_not_a_blank_grid(self, tmp_path: Path) -> None:
        empty = LocalTapi(StoreBuilder(tmp_path / "e.db").store)
        rows = empty.series(None, "sys:port_summary", as_of=LATER)
        assert rows and isinstance(rows[0][0], str)


class TestTheVcubPanels:
    """The vol grid and the note explaining how it was built."""

    def test_the_grid_carries_a_vol_and_the_prints_behind_it(self, tmp_path: Path) -> None:
        """Each cell is an expiry/tenor bucket with a fitted normal vol and
        the number of prints that produced it. A vol with no print count is
        a figure a trader cannot weigh."""
        tapi = LocalTapi(StoreBuilder(tmp_path / "v.db").with_curves().with_swaptions().store)
        rows = tapi.series(None, "sys:vcub_grid", as_of=LATER)
        assert {str(r[0]) for r in rows} == {"1Y", "2Y", "5Y"}
        assert all(float(r[2]) > 0.0 for r in rows)  # type: ignore[arg-type]
        assert sum(int(r[3]) for r in rows) == 40  # type: ignore[arg-type]

    def test_the_method_panel_dates_the_surface(self, tmp_path: Path) -> None:
        """A surface undated is a surface from any day, and vol from a
        fortnight ago prices nothing today."""
        tapi = LocalTapi(StoreBuilder(tmp_path / "vm.db").with_curves().with_swaptions().store)
        rows = dict(tapi.series(None, "sys:vcub_method", as_of=LATER))  # type: ignore[arg-type]
        assert rows["As of"] == "2026-07-31"

    def test_no_prints_is_a_stated_reason(self, tmp_path: Path) -> None:
        empty = LocalTapi(StoreBuilder(tmp_path / "ev.db").store)
        rows = empty.series(None, "sys:vcub_grid", as_of=LATER)
        assert rows and isinstance(rows[0][0], str)


class TestTheAllqPanels:
    """ALLQ reads a live contribution service, not the store — so nothing
    the builder writes reaches it, and its populated path had never run."""

    @staticmethod
    def _tapi(tmp_path: Path, *, quotes: bool) -> LocalTapi:
        from datetime import timedelta

        from treble.tapi.contribution import (
            ContributionRequest,
            ContributionService,
            Firmness,
        )
        from treble.tapi.local import TickerIndex

        # A TTL wide enough that the fixture's quotes are still live at
        # LATER: the default is five minutes, under which every quote here
        # would expire and the populated case would silently become the
        # empty one.
        service = ContributionService(ttl=timedelta(days=365))
        if quotes:
            for i, (who, firm) in enumerate(
                (("BANK-A", Firmness.EXECUTABLE), ("BANK-B", Firmness.INDICATIVE))
            ):
                service.contribute(
                    ContributionRequest(
                        subject="cik:0000051143",
                        contributor=who,
                        firmness=firm,
                        bid=99.10 + i * 0.05,
                        ask=99.40 + i * 0.05,
                        bid_size=2e6,
                        ask_size=2e6,
                    ),
                    received_at=LATER,
                )
        return LocalTapi(
            StoreBuilder(tmp_path / f"a-{quotes}.db").store,
            tickers=TickerIndex({"IBM": 51143}),
            contributions=service,
        )

    @staticmethod
    def _ibm() -> object:
        from treble.core.identifiers import SecurityQuery, YellowKey

        return SecurityQuery(ticker="IBM", key=YellowKey.EQUITY, venue=None, descriptor=None)

    def test_every_contributor_appears_with_its_own_two_sided_price(self, tmp_path: Path) -> None:
        rows = self._tapi(tmp_path, quotes=True).series(  # type: ignore[arg-type]
            self._ibm(), "sys:allq", as_of=LATER
        )
        assert [r[0] for r in rows] == ["BANK-A", "BANK-B"]
        assert [r[1] for r in rows] == ["executable", "indicative"]
        assert rows[0][2] == pytest.approx(99.10)

    def test_the_composites_separate_executable_from_indicative(self, tmp_path: Path) -> None:
        """TCMP is what someone will trade on; TGN includes talk. Building
        both from every quote would make the distinction decorative, and
        the fixture contributes one of each so the two must differ.
        """
        rows = {
            str(r[0]): r[1]
            for r in self._tapi(tmp_path, quotes=True).series(  # type: ignore[arg-type]
                self._ibm(), "sys:allq_composites", as_of=LATER
            )
        }
        assert rows["TCMP (executable)"] == pytest.approx(99.10)
        assert rows["TGN (indicative)"] != rows["TCMP (executable)"]
        assert rows["Contributors"] == 2.0

    def test_an_empty_book_says_how_long_it_has_been_empty(self, tmp_path: Path) -> None:
        """The Phase 2 criterion is ALLQ correct-when-empty. A screen that
        cannot distinguish "no one has ever quoted this" from "the feed
        died" is the failure this row exists to prevent."""
        rows = {
            str(r[0]): r[1]
            for r in self._tapi(tmp_path, quotes=False).series(  # type: ignore[arg-type]
                self._ibm(), "sys:allq_composites", as_of=LATER
            )
        }
        assert rows["Contributors"] == 0.0
        assert rows["Last live"] == "never"


class TestTheProductCatalogueIsMeasuredNotAsserted:
    """`sys:swpm_products`.

    The status column was a hardcoded string, and on the live store one of
    them read "priceable — HICP stored" while the store held no inflation
    facts at all. Nothing was wrong with the pricer; the catalogue had
    never asked. A claim about a user's own data that is written rather
    than measured is the same defect as a test that cannot fail, and worse,
    because it renders as a fact about their install.
    """

    def test_an_empty_store_calls_nothing_priceable(self, tmp_path: Path) -> None:
        """The assertion that would have caught it. Every product needs at
        least a curve, so on an empty store every row must refuse."""
        empty = LocalTapi(StoreBuilder(tmp_path / "p.db").store)
        rows = empty.series(None, "sys:swpm_products", as_of=LATER)
        assert len(rows) == 7
        assert all("not priceable" in str(r[2]) for r in rows)
        assert any("no swap curves" in str(r[2]) for r in rows)

    def test_curves_alone_make_the_vol_products_priceable(self, populated: LocalTapi) -> None:
        """CAP/FLOOR and CMS need only a curve environment and a vol the
        caller states, so they flip on curves alone."""
        rows = {
            str(r[0]): str(r[2]) for r in populated.series(None, "sys:swpm_products", as_of=LATER)
        }
        assert rows["CAP / FLOOR"].startswith("priceable")
        assert rows["CMS"].startswith("priceable")

    def test_inflation_refuses_until_an_index_is_ingested(self, populated: LocalTapi) -> None:
        """The specific row that was lying. The builder writes curves and
        bonds but no HICP, so this must still refuse — and name what is
        missing rather than saying "unavailable"."""
        rows = {
            str(r[0]): str(r[2]) for r in populated.series(None, "sys:swpm_products", as_of=LATER)
        }
        assert "not priceable" in rows["INFLATION ZC"]
        assert "inflation:EUR:HICP" in rows["INFLATION ZC"]

    def test_swaption_prints_flip_the_cancellable_row(self, tmp_path: Path) -> None:
        """Proof the probe reads the store rather than a constant: the same
        row must answer differently on two stores that differ only in
        whether swaption prints were ingested."""
        without = LocalTapi(StoreBuilder(tmp_path / "a.db").with_curves().store)
        with_prints = LocalTapi(
            StoreBuilder(tmp_path / "b.db").with_curves().with_swaptions().store
        )

        def row(tapi: LocalTapi) -> str:
            return next(
                str(r[2])
                for r in tapi.series(None, "sys:swpm_products", as_of=LATER)
                if str(r[0]) == "CANCELLABLE"
            )

        assert "not priceable" in row(without)
        assert row(with_prints).startswith("priceable")


class TestBondsResolveByTheIdentifierTheFilingsCarry:
    """Resolution accepted CUSIPs only, and N-PORT publishes ISINs.

    The live store held 1,861 ISIN subjects against 147 CUSIPs, so 93% of
    the bond universe was addressable only by an identifier no source in
    the system writes — and the 373,125-fact GLEIF relationship graph
    behind those issuers could not be reached from any screen at all.
    """

    @staticmethod
    def _tapi(tmp_path: Path) -> LocalTapi:
        return LocalTapi(StoreBuilder(tmp_path / "i.db").with_bonds(count=4).store)

    @staticmethod
    def _query(ticker: str) -> object:
        from treble.core.identifiers import SecurityQuery, YellowKey

        return SecurityQuery(ticker=ticker, key=YellowKey.CORP, venue=None, descriptor=None)

    def test_an_isin_resolves_to_the_stored_bond(self, tmp_path: Path) -> None:
        from treble.core.identifiers import isin_from_cusip

        isin = isin_from_cusip("000000000")
        assert str(self._tapi(tmp_path).resolve(self._query(isin))) == f"isin:{isin}"  # type: ignore[arg-type]

    def test_a_bare_cusip_finds_the_bond_stored_under_its_isin(self, tmp_path: Path) -> None:
        """The reverse bridge, and the one a trader actually needs: they
        type a CUSIP, the filing wrote an ISIN."""
        from treble.core.identifiers import isin_from_cusip

        resolved = self._tapi(tmp_path).resolve(self._query("000000000"))  # type: ignore[arg-type]
        assert str(resolved) == f"isin:{isin_from_cusip('000000000')}"

    def test_an_unknown_isin_says_it_was_not_ingested(self, tmp_path: Path) -> None:
        """Distinct from "no resolution for this namespace", which was the
        old answer and sent a reader looking for an unbuilt feature rather
        than an unloaded bond."""
        from treble.tapi.local import SecurityNotFoundError

        with pytest.raises(SecurityNotFoundError, match="ISIN has not been ingested"):
            self._tapi(tmp_path).resolve(self._query("US0378331005"))  # type: ignore[arg-type]

    def test_a_malformed_identifier_is_not_treated_as_an_isin(self, tmp_path: Path) -> None:
        """`IBM 4.15 05/15/39 Corp` is a valid bond reference whose ticker
        is "IBM". Treating every Corp ticker as an identifier is the defect
        the CUSIP path already had to fix."""
        from treble.tapi.local import SecurityNotFoundError

        with pytest.raises(SecurityNotFoundError):
            self._tapi(tmp_path).resolve(self._query("IBM"))  # type: ignore[arg-type]


class TestTheEmptyAllqBookStatesItself:
    """The criterion is ALLQ *correct-when-empty*, and half the screen was
    getting it right: composites reported "Contributors 0 / Last live
    never" while the quote pane returned zero rows, which renders as a
    blank pane indistinguishable from one that failed to load."""

    def test_no_contributors_gives_a_reason_not_an_empty_table(self, tmp_path: Path) -> None:
        from treble.core.identifiers import SecurityQuery, YellowKey
        from treble.tapi.local import TickerIndex

        tapi = LocalTapi(
            StoreBuilder(tmp_path / "q.db").store,
            tickers=TickerIndex({"IBM": 51143}),
        )
        rows = tapi.series(
            SecurityQuery(ticker="IBM", key=YellowKey.EQUITY, venue=None, descriptor=None),
            "sys:allq",
            as_of=LATER,
        )
        assert len(rows) == 1
        assert "no contributor is quoting" in str(rows[0][0])
        assert "never quoted" in str(rows[0][0])
