"""Model-derived bond fields behind YAS.

Two failures these exist for, both of which produced numbers rather than
errors:

- **The risk measures take a yield, not a price.** Passing the clean price
  does not raise: QuantLib reads 98.88 as a 9888% yield and returns a
  modified duration of 0.006 for a twenty-year bond. Small, plausible, and
  wrong by three orders of magnitude.
- **A TIPS priced with nominal maths returns a real yield.** It looks like a
  nominal yield and sits happily beside them. Refusal is the only honest
  answer until index-ratio handling exists.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import parse_security
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.local import LocalTapi, SecurityNotFoundError

AS_OF = datetime(2026, 4, 30, 12, 0, tzinfo=UTC)

#: A real 20-year auction: 912810UT3, 4.625% of 2046-02-15, stop-out price
#: 96.735962 for a published high yield of 4.883%.
TERMS: dict[str, object] = {
    "int_rate": 4.625,
    "high_price": 96.735962,
    "high_yield": 4.883,
    "maturity_date": date(2046, 2, 15),
    "dated_date": date(2026, 2, 15),
    "issue_date": date(2026, 4, 30),
    "inflation_index_security": "No",
}


def _store(tmp_path: Path, terms: dict[str, object], cusip: str = "912810UT3") -> DuckStore:
    store = DuckStore(tmp_path / "bonds.db")
    record = Provenance(
        source_system="treasury",
        source_uri="https://api.fiscaldata.treasury.gov/…/auctions_query",
        retrieved_at=AS_OF,
        method=ExtractionMethod.API,
        extractor_version="1",
    )
    store.write_provenance([record])
    store.write_facts(
        [
            Fact(
                subject=f"cusip:{cusip}",
                field=field,
                value=value,  # type: ignore[arg-type]
                effective_from=date(2026, 4, 23),
                knowledge_from=datetime(2026, 4, 24, tzinfo=UTC),
                provenance_id=record.id,
            )
            for field, value in terms.items()
        ]
    )
    return store


@pytest.fixture
def tapi(tmp_path: Path) -> LocalTapi:
    return LocalTapi(_store(tmp_path, TERMS))


def _value(tapi: LocalTapi, mnemonic: str, cusip: str = "912810UT3") -> object:
    return tapi.field(parse_security(f"{cusip} Govt"), mnemonic, {}, as_of=AS_OF).value


class TestAnalyticsAreCorrect:
    def test_yield_matches_the_published_auction_yield(self, tapi: LocalTapi) -> None:
        """Treasury published 4.883% for this price. Computed independently."""
        assert abs(float(_value(tapi, "YLD_YTM_MID")) - 4.883) * 100 < 0.25  # type: ignore[arg-type]

    def test_modified_duration_is_in_the_right_order_of_magnitude(self, tapi: LocalTapi) -> None:
        """The regression test for the yield-vs-price bug. A twenty-year
        bond has a duration near twelve; the bug returned 0.006, and no
        assertion on 'is it a number' would have caught that."""
        assert 10.0 < float(_value(tapi, "DUR_ADJ_MID")) < 15.0  # type: ignore[arg-type]

    def test_dv01_reconciles_with_duration_and_price(self, tapi: LocalTapi) -> None:
        """DV01 ~ modified duration x dirty price / 10,000. Ties two
        independently computed measures together, so a unit error in either
        shows up here rather than on screen."""
        duration = float(_value(tapi, "DUR_ADJ_MID"))  # type: ignore[arg-type]
        dv01 = float(_value(tapi, "DV01"))  # type: ignore[arg-type]
        assert dv01 == pytest.approx(duration * 96.735962 / 10_000, rel=0.05)

    def test_convexity_is_positive_for_a_bullet(self, tapi: LocalTapi) -> None:
        assert float(_value(tapi, "CNVX_MID")) > 0  # type: ignore[arg-type]

    def test_workout_is_maturity_for_a_bullet(self, tapi: LocalTapi) -> None:
        assert _value(tapi, "WORKOUT_DT_MID") == "2046-02-15"

    def test_computed_values_are_marked_model_derived(self, tapi: LocalTapi) -> None:
        """§5.4: a model output must never look like a reported figure."""
        result = tapi.field(parse_security("912810UT3 Govt"), "YLD_YTM_MID", {}, as_of=AS_OF)
        assert result.model_derived


class TestInflationIndexedSecuritiesAreRefused:
    @pytest.fixture
    def tips(self, tmp_path: Path) -> LocalTapi:
        return LocalTapi(_store(tmp_path, {**TERMS, "inflation_index_security": "Yes"}))

    @pytest.mark.parametrize(
        "mnemonic", ["YLD_YTM_MID", "DUR_ADJ_MID", "CNVX_MID", "DV01", "WORKOUT_DT_MID"]
    )
    def test_every_analytic_is_null(self, tips: LocalTapi, mnemonic: str) -> None:
        assert _value(tips, mnemonic) is None

    def test_the_published_terms_still_show(self, tips: LocalTapi) -> None:
        """Refusing to compute is not refusing to display: what the issuer
        published is still shown, so the screen explains itself."""
        assert _value(tips, "high_yield") == 1.955 or _value(tips, "high_yield") == 4.883
        assert _value(tips, "inflation_index_security") == "Yes"


class TestMissingInputs:
    def test_incomplete_terms_give_null_not_a_guess(self, tmp_path: Path) -> None:
        """A duration computed from a defaulted coupon is worse than a dash:
        the dash is visibly missing, the number is invisibly wrong."""
        partial = {k: v for k, v in TERMS.items() if k != "int_rate"}
        tapi = LocalTapi(_store(tmp_path, partial))
        assert _value(tapi, "DUR_ADJ_MID") is None

    def test_unknown_cusip_is_rejected_at_resolution(self, tapi: LocalTapi) -> None:
        """A mistyped CUSIP must not resolve to an empty subject and render
        a full screen of dashes that looks like a real bond with no data."""
        with pytest.raises(SecurityNotFoundError, match="has not been ingested"):
            tapi.resolve(parse_security("000000000 Govt"))
