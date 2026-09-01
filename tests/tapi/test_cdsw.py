"""`CDSW` — what the tape said, and what the model makes of it.

Two panes and not one, because merging them lets a reader take a hazard rate
for an observation. The tests here are mostly about the boundary between
them: a tenor the tape quoted only as an upfront must appear in the pricing
pane *saying so*, never with an implied spread, because implying one needs a
discount curve and a solve and would put a number on the screen that no
source stated.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.cdsw import (
    CURVE_HEADER,
    FLAT_DISCOUNT,
    PRICING_HEADER,
    default_entity,
    entity_curve,
    entity_pricing,
    method,
)

AS_OF = datetime(2026, 9, 2, tzinfo=UTC)
REPORT = date(2026, 8, 28)
ENTITY = "cds:redid:FF667M"


def _store(tmp_path: Path, points: dict[str, dict[str, float]], name: str = "AXA") -> DuckStore:
    """A store holding one entity's curve, as the adapter would write it."""
    store = DuckStore(tmp_path / "s.db")
    record = Provenance(
        source_system="dtcc-credit",
        source_uri="https://example.invalid/SEC_CUMULATIVE_CREDITS_2026_08_28.zip",
        retrieved_at=datetime(2026, 8, 29, tzinfo=UTC),
        method=ExtractionMethod.API,
        extractor_version="1",
    )
    store.write_provenance([record])
    facts = [
        Fact(
            subject=TUID(ENTITY),
            field="REFERENCE_ENTITY",
            value=name,
            effective_from=REPORT,
            effective_to=REPORT,
            knowledge_from=datetime(2026, 8, 29, tzinfo=UTC),
            provenance_id=record.id,
        )
    ]
    for tenor, fields in points.items():
        for field, value in fields.items():
            facts.append(
                Fact(
                    subject=TUID(f"{ENTITY}:{tenor}"),
                    field=field,
                    value=value,
                    effective_from=REPORT,
                    effective_to=REPORT,
                    knowledge_from=datetime(2026, 8, 29, tzinfo=UTC),
                    provenance_id=record.id,
                )
            )
    store.write_facts(facts)
    return store


def _cell(rows: list, column: str, header: tuple[str, ...], row: int = 1) -> object:
    """One cell by column *name*.

    Positional indices broke three tests the moment a column was inserted,
    and they broke by shifting rather than by failing to find anything —
    which is the same class of error as the row that carried five cells
    against a six-column header.
    """
    return rows[row][header.index(column)]


QUOTED = {"PAR_SPREAD": 0.0575, "CDS_COUPON": 0.05, "TRADE_COUNT": 4.0}
UPFRONT_ONLY = {"CDS_COUPON": 0.05, "UPFRONT_FRACTION": 0.02377, "TRADE_COUNT": 2.0}


class TestTheObservedCurve:
    def test_it_leads_with_a_header(self, tmp_path: Path) -> None:
        rows = entity_curve(_store(tmp_path, {"5Y": QUOTED}), ENTITY, as_of=AS_OF)
        assert rows[0] == CURVE_HEADER

    def test_a_spread_is_shown_in_basis_points(self, tmp_path: Path) -> None:
        rows = entity_curve(_store(tmp_path, {"5Y": QUOTED}), ENTITY, as_of=AS_OF)
        assert _cell(rows, "Spread bp", CURVE_HEADER) == pytest.approx(575.0)

    def test_tenors_are_ordered_by_years_not_by_text(self, tmp_path: Path) -> None:
        """`10Y` sorts before `2Y` as a string. A curve out of order reads
        as inverted."""
        store = _store(tmp_path, {"2Y": QUOTED, "10Y": QUOTED, "1Y": QUOTED})
        assert [r[0] for r in entity_curve(store, ENTITY, as_of=AS_OF)[1:]] == ["1Y", "2Y", "10Y"]

    def test_an_entity_with_nothing_says_so(self, tmp_path: Path) -> None:
        """`ALLQ correct-when-empty` is a Phase 2 criterion; the same rule
        applies here. A blank pane and a missing entity look identical."""
        rows = entity_curve(_store(tmp_path, {}), ENTITY, as_of=AS_OF)
        assert len(rows) == 2
        assert "no CDS prints" in str(rows[1][0])

    def test_a_capped_count_is_carried(self, tmp_path: Path) -> None:
        """So a thin point looks thin rather than quietly so."""
        store = _store(tmp_path, {"5Y": {**QUOTED, "CAPPED_TRADE_COUNT": 3.0}})
        rows = entity_curve(store, ENTITY, as_of=AS_OF)
        assert _cell(rows, "Capped", CURVE_HEADER) == pytest.approx(3.0)


class TestPricing:
    def test_a_quoted_tenor_is_priced(self, tmp_path: Path) -> None:
        rows = entity_pricing(_store(tmp_path, {"5Y": QUOTED}), ENTITY, as_of=AS_OF)
        assert rows[1][0] == "5Y"
        hazard = _cell(rows, "Hazard bp", PRICING_HEADER)
        assert isinstance(hazard, float) and hazard > 0

    def test_the_model_is_named_on_every_priced_row(self, tmp_path: Path) -> None:
        """I3: no analytic returns a bare number."""
        rows = entity_pricing(_store(tmp_path, {"5Y": QUOTED}), ENTITY, as_of=AS_OF)
        assert _cell(rows, "Model", PRICING_HEADER) == "credit.price_cds"

    def test_an_upfront_only_tenor_is_implied_and_says_so(self, tmp_path: Path) -> None:
        """The boundary this file exists for. Omitting the tenor would make
        a five-tenor curve read as a three-tenor one; showing an implied
        spread without saying so would state the model's number as the
        market's."""
        rows = entity_pricing(_store(tmp_path, {"5Y": UPFRONT_ONLY}), ENTITY, as_of=AS_OF)
        assert _cell(rows, "Model", PRICING_HEADER) == "credit.spread_from_upfront"

    def test_a_spread_below_the_coupon_gives_a_negative_upfront(self, tmp_path: Path) -> None:
        """The protection seller pays. A sign error here would invert who
        owes whom, and the number would still look plausible."""
        cheap = {"PAR_SPREAD": 0.018, "CDS_COUPON": 0.05, "TRADE_COUNT": 2.0}
        rows = entity_pricing(_store(tmp_path, {"1Y": cheap}), ENTITY, as_of=AS_OF)
        upfront = _cell(rows, "Upfront %", PRICING_HEADER)
        assert isinstance(upfront, float) and upfront < 0

    def test_a_spread_above_the_coupon_gives_a_positive_upfront(self, tmp_path: Path) -> None:
        """Proves the sign turns on the spread rather than always being
        negative."""
        rows = entity_pricing(_store(tmp_path, {"5Y": QUOTED}), ENTITY, as_of=AS_OF)
        upfront = _cell(rows, "Upfront %", PRICING_HEADER)
        assert isinstance(upfront, float) and upfront > 0

    def test_the_header_comes_first(self, tmp_path: Path) -> None:
        rows = entity_pricing(_store(tmp_path, {"5Y": QUOTED}), ENTITY, as_of=AS_OF)
        assert rows[0] == PRICING_HEADER


class TestTheMethodPane:
    def test_it_states_the_discount_assumption(self) -> None:
        """A CDS upfront is sensitive to it, and a reader who cannot see the
        assumption cannot judge the number."""
        text = " ".join(str(cell) for row in method(ENTITY) for cell in row)
        assert f"{FLAT_DISCOUNT:.1%}" in text

    def test_it_says_the_hazard_is_an_approximation(self) -> None:
        text = " ".join(str(cell) for row in method(ENTITY) for cell in row)
        assert "approximation" in text

    def test_it_names_the_redistribution_restriction(self) -> None:
        """Markit RED codes and unverified DTCC terms — the same guard the
        CUSIP rule exists for (§9.3)."""
        text = " ".join(str(cell) for row in method(ENTITY) for cell in row)
        assert "restricted" in text.lower()


class TestChoosingAnEntity:
    def test_the_deepest_curve_wins(self, tmp_path: Path) -> None:
        """A screen opening on a single-point curve reads as broken."""
        store = _store(tmp_path, {"1Y": QUOTED, "3Y": QUOTED, "5Y": QUOTED})
        store.write_provenance([])
        assert default_entity(store, as_of=AS_OF) == ENTITY

    def test_an_empty_store_yields_nothing(self, tmp_path: Path) -> None:
        assert default_entity(DuckStore(tmp_path / "e.db"), as_of=AS_OF) is None


class TestEveryRowFillsItsColumns:
    """A short row does not leave a blank; it shifts everything after it.

    The "no spread quoted" row carried five cells against a six-column
    header, so its reason rendered in the `Upfront %` column — a sentence
    where a number belongs, and every column after it wrong.
    """

    def test_a_priced_row_matches_the_header(self, tmp_path: Path) -> None:
        rows = entity_pricing(_store(tmp_path, {"5Y": QUOTED}), ENTITY, as_of=AS_OF)
        assert all(len(row) == len(PRICING_HEADER) for row in rows)

    def test_an_upfront_only_row_matches_the_header(self, tmp_path: Path) -> None:
        rows = entity_pricing(_store(tmp_path, {"5Y": UPFRONT_ONLY}), ENTITY, as_of=AS_OF)
        assert all(len(row) == len(PRICING_HEADER) for row in rows)

    def test_a_row_with_neither_matches_the_header(self, tmp_path: Path) -> None:
        rows = entity_pricing(_store(tmp_path, {"5Y": {"TRADE_COUNT": 2.0}}), ENTITY, as_of=AS_OF)
        assert all(len(row) == len(PRICING_HEADER) for row in rows)
        assert "neither" in str(rows[1][-1])

    def test_every_curve_row_matches_its_header(self, tmp_path: Path) -> None:
        rows = entity_curve(_store(tmp_path, {"5Y": QUOTED}), ENTITY, as_of=AS_OF)
        assert all(len(row) == len(CURVE_HEADER) for row in rows)


class TestAnUpfrontOnlyTenorIsImplied:
    """The solve, seen from the screen."""

    def test_it_gets_a_spread(self, tmp_path: Path) -> None:
        rows = entity_pricing(_store(tmp_path, {"5Y": UPFRONT_ONLY}), ENTITY, as_of=AS_OF)
        spread = _cell(rows, "Spread bp", PRICING_HEADER)
        assert isinstance(spread, float) and spread > 0

    def test_the_model_says_it_was_implied(self, tmp_path: Path) -> None:
        """The whole reason quoted and implied can share a column: the id
        travels with the number and says which claim it is."""
        rows = entity_pricing(_store(tmp_path, {"5Y": UPFRONT_ONLY}), ENTITY, as_of=AS_OF)
        assert _cell(rows, "Model", PRICING_HEADER) == "credit.spread_from_upfront"

    def test_a_quoted_tenor_says_it_was_not(self, tmp_path: Path) -> None:
        rows = entity_pricing(_store(tmp_path, {"5Y": QUOTED}), ENTITY, as_of=AS_OF)
        assert _cell(rows, "Model", PRICING_HEADER) == "credit.price_cds"

    def test_the_observed_pane_still_shows_no_spread(self, tmp_path: Path) -> None:
        """The implied number belongs to the model, not to the tape. The
        first pane is what was reported and must stay that way."""
        rows = entity_curve(_store(tmp_path, {"5Y": UPFRONT_ONLY}), ENTITY, as_of=AS_OF)
        assert _cell(rows, "Spread bp", CURVE_HEADER) is None


class TestAnEntityWithNothingLeftIsNotChosen:
    def test_a_fully_retracted_entity_is_skipped(self, tmp_path: Path) -> None:
        """A subject exists for as long as anything was ever written to it,
        so counting subjects opened the screen on an entity whose every cell
        was an em dash — which is what the retracted placeholder ISINs
        became."""
        store = _store(tmp_path, {"5Y": {"TRADE_COUNT": 2.0}})
        assert default_entity(store, as_of=AS_OF) is None

    def test_an_entity_with_a_spread_is_chosen(self, tmp_path: Path) -> None:
        assert default_entity(_store(tmp_path, {"5Y": QUOTED}), as_of=AS_OF) == ENTITY

    def test_an_entity_with_only_an_upfront_is_chosen(self, tmp_path: Path) -> None:
        """It has something to say — the solve turns it into a spread."""
        assert default_entity(_store(tmp_path, {"5Y": UPFRONT_ONLY}), as_of=AS_OF) == ENTITY
