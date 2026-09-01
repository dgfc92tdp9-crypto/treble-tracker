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


QUOTED = {"PAR_SPREAD": 0.0575, "CDS_COUPON": 0.05, "TRADE_COUNT": 4.0}
UPFRONT_ONLY = {"CDS_COUPON": 0.05, "UPFRONT_FRACTION": 0.02377, "TRADE_COUNT": 2.0}


class TestTheObservedCurve:
    def test_it_leads_with_a_header(self, tmp_path: Path) -> None:
        rows = entity_curve(_store(tmp_path, {"5Y": QUOTED}), ENTITY, as_of=AS_OF)
        assert rows[0] == CURVE_HEADER

    def test_a_spread_is_shown_in_basis_points(self, tmp_path: Path) -> None:
        rows = entity_curve(_store(tmp_path, {"5Y": QUOTED}), ENTITY, as_of=AS_OF)
        assert rows[1][1] == pytest.approx(575.0)

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
        assert entity_curve(store, ENTITY, as_of=AS_OF)[1][5] == pytest.approx(3.0)


class TestPricing:
    def test_a_quoted_tenor_is_priced(self, tmp_path: Path) -> None:
        rows = entity_pricing(_store(tmp_path, {"5Y": QUOTED}), ENTITY, as_of=AS_OF)
        assert rows[1][0] == "5Y"
        assert isinstance(rows[1][1], float) and rows[1][1] > 0

    def test_the_model_is_named_on_every_priced_row(self, tmp_path: Path) -> None:
        """I3: no analytic returns a bare number."""
        rows = entity_pricing(_store(tmp_path, {"5Y": QUOTED}), ENTITY, as_of=AS_OF)
        assert rows[1][4] == "credit.price_cds"

    def test_an_upfront_only_tenor_is_listed_with_a_reason(self, tmp_path: Path) -> None:
        """The boundary this file exists for. Omitting it would make a
        five-tenor curve read as a three-tenor one; implying a spread would
        state a number no source gave."""
        rows = entity_pricing(_store(tmp_path, {"5Y": UPFRONT_ONLY}), ENTITY, as_of=AS_OF)
        assert rows[1][1] is None
        assert "no spread quoted" in str(rows[1][4])

    def test_a_spread_below_the_coupon_gives_a_negative_upfront(self, tmp_path: Path) -> None:
        """The protection seller pays. A sign error here would invert who
        owes whom, and the number would still look plausible."""
        cheap = {"PAR_SPREAD": 0.018, "CDS_COUPON": 0.05, "TRADE_COUNT": 2.0}
        rows = entity_pricing(_store(tmp_path, {"1Y": cheap}), ENTITY, as_of=AS_OF)
        assert isinstance(rows[1][3], float) and rows[1][3] < 0

    def test_a_spread_above_the_coupon_gives_a_positive_upfront(self, tmp_path: Path) -> None:
        """Proves the sign turns on the spread rather than always being
        negative."""
        rows = entity_pricing(_store(tmp_path, {"5Y": QUOTED}), ENTITY, as_of=AS_OF)
        assert isinstance(rows[1][3], float) and rows[1][3] > 0

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
