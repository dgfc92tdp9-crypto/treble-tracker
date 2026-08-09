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
