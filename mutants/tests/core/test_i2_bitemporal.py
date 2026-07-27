"""I2 kill-tests, model level: facts are immutable and bitemporal.

The query-layer half of I2 (required as_of, no row with knowledge_from >
as_of, latest-knowledge-wins) lives in tests/store/ once the store exists.
"""

from datetime import UTC, date, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ProvenanceId

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
PID = ProvenanceId("p" * 64)


def make_fact(**overrides: object) -> Fact:
    defaults: dict[str, object] = {
        "subject": TUID("t1"),
        "field": "REVENUE",
        "value": 1_000_000.0,
        "effective_from": date(2026, 3, 31),
        "effective_to": date(2026, 3, 31),
        "knowledge_from": NOW,
        "provenance_id": PID,
    }
    defaults.update(overrides)
    return Fact.model_validate(defaults)


class TestImmutability:
    def test_fact_is_frozen(self) -> None:
        fact = make_fact()
        with pytest.raises(ValidationError):
            fact.value = 2.0  # type: ignore[misc]
        with pytest.raises(ValidationError):
            fact.knowledge_from = NOW  # type: ignore[misc]

    def test_fact_has_no_knowledge_to_field(self) -> None:
        # ADR-0001: knowledge_to is derived at query time, never stored.
        assert "knowledge_to" not in Fact.model_fields


class TestTemporalValidation:
    def test_naive_knowledge_from_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_fact(knowledge_from=datetime(2026, 7, 25, 12, 0))  # noqa: DTZ001

    def test_effective_range_ordered(self) -> None:
        with pytest.raises(ValidationError):
            make_fact(effective_from=date(2026, 3, 31), effective_to=date(2026, 1, 1))

    def test_open_ended_effective_allowed(self) -> None:
        assert make_fact(effective_to=None).effective_to is None

    def test_null_value_allowed_with_provenance(self) -> None:
        # Spec working agreement: unavailable source => value is null and
        # provenance says why. Null is representable; fabrication is not.
        assert make_fact(value=None).value is None


@given(
    effective_from=st.dates(min_value=date(1990, 1, 1), max_value=date(2030, 12, 31)),
    span_days=st.integers(min_value=0, max_value=3650),
)
def test_effective_ranges_always_ordered(effective_from: date, span_days: int) -> None:
    from datetime import timedelta

    fact = make_fact(
        effective_from=effective_from,
        effective_to=effective_from + timedelta(days=span_days),
    )
    assert fact.effective_to is not None
    assert fact.effective_from <= fact.effective_to
