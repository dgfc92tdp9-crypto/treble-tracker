"""Model-derived fields in TQL, and the overrides they run under (§4.2).

The spec calls overrides "the mechanism by which the entire analytics
library is exposed as data". An override that parsed but never reached the
model would return a number computed under conditions nobody asked for —
identical in appearance to one that honoured it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.tql.execute import execute, plan
from treble.tql.grammar import Value, parse_tql

AS_OF = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


class FakeSource:
    def __init__(self, facts: list[Fact]) -> None:
        self._facts = facts

    def read(self, subject: TUID, field: str, *, as_of: datetime) -> list[Fact]:
        return [f for f in self._facts if f.subject == subject and f.field == field]

    def subjects_with_prefix(self, prefix: str, *, as_of: datetime) -> list[TUID]:
        return sorted({f.subject for f in self._facts if f.subject.startswith(prefix)})


class RecordingModels:
    """Records what it was asked, so the override can be asserted on."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[tuple[str, Value], ...]]] = []

    def compute(
        self,
        subject: TUID,
        mnemonic: str,
        overrides: tuple[tuple[str, Value], ...],
        *,
        as_of: datetime,
    ) -> tuple[object, str | None] | None:
        if not mnemonic.startswith("OAS"):
            return None  # not model-derived; fall through to the store
        self.calls.append((mnemonic, overrides))
        vol = dict(overrides).get("vol_override", 0.10)
        return 100.0 * float(vol), "model-provenance"  # type: ignore[arg-type]


@pytest.fixture
def source() -> FakeSource:
    return FakeSource(
        [
            Fact(
                subject="cusip:AAA",
                field="int_rate",
                value=4.5,
                effective_from=date(2026, 1, 1),
                knowledge_from=datetime(2026, 1, 2, tzinfo=UTC),
                provenance_id="p" * 12,
            )
        ]
    )


def _run(text: str, source: FakeSource, models: RecordingModels):  # type: ignore[no-untyped-def]
    return execute(plan(parse_tql(text), as_of=AS_OF), source, models)


class TestOverridesReachTheModel:
    def test_the_override_is_passed_through(self, source: FakeSource) -> None:
        models = RecordingModels()
        _run("get(oas_spread_mid(vol_override=0.20)) for(bonds())", source, models)
        assert models.calls == [("OAS_SPREAD_MID", (("vol_override", 0.20),))]

    def test_the_override_changes_the_answer(self, source: FakeSource) -> None:
        """The test that would fail if the override were parsed and dropped:
        both queries are otherwise identical."""
        models = RecordingModels()
        default = _run("get(oas_spread_mid) for(bonds())", source, models)
        overridden = _run("get(oas_spread_mid(vol_override=0.20)) for(bonds())", source, models)
        assert dict(default.rows[0].values)["oas_spread_mid"] == pytest.approx(10.0)
        assert dict(overridden.rows[0].values)["oas_spread_mid"] == pytest.approx(20.0)

    def test_computed_fields_are_marked(self, source: FakeSource) -> None:
        """§5.4: a computed number must not render like a reported one."""
        result = _run("get(int_rate, oas_spread_mid) for(bonds())", source, RecordingModels())
        assert result.rows[0].model_derived == ("oas_spread_mid",)

    def test_a_model_value_carries_its_own_provenance(self, source: FakeSource) -> None:
        result = _run("get(oas_spread_mid) for(bonds())", source, RecordingModels())
        assert dict(result.rows[0].provenance)["oas_spread_mid"] == "model-provenance"


class TestFallThrough:
    def test_a_stored_field_is_not_sent_to_the_models(self, source: FakeSource) -> None:
        models = RecordingModels()
        result = _run("get(int_rate) for(bonds())", source, models)
        assert models.calls == []
        assert dict(result.rows[0].values)["int_rate"] == 4.5

    def test_queries_still_run_with_no_model_source(self, source: FakeSource) -> None:
        """TQL must work against stored facts alone — which is also what
        lets the executor be tested without the analytics stack."""
        result = execute(plan(parse_tql("get(int_rate) for(bonds())"), as_of=AS_OF), source)
        assert dict(result.rows[0].values)["int_rate"] == 4.5


class TestUnknownOverridesAreRejected:
    def test_an_override_the_model_does_not_accept_raises(self) -> None:
        """Silently dropping it would return a number computed under
        conditions nobody asked for, indistinguishable from one that was."""
        from treble.tapi.local import TapiModelSource, UnknownOverrideError

        source = TapiModelSource.__new__(TapiModelSource)  # no store needed
        with pytest.raises(UnknownOverrideError, match="does not accept"):
            source.compute(TUID("cusip:AAA"), "YLD_YTM_MID", (("nonsense", 1),), as_of=AS_OF)
