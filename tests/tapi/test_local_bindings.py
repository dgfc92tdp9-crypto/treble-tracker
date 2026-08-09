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
