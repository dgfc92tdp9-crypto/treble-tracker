"""I3 kill-tests: every public analytic is registered and returns an envelope.

The registry walk fails CI the moment someone adds a public analytics
callable without @model — remove the decorator from any analytic and
test_all_public_analytics_are_registered fails.
"""

import importlib
import inspect
import pkgutil

import pytest

import treble.analytics
from treble.analytics.registry import (
    MODEL_REGISTRY,
    ModelResult,
    is_registered_model,
    model,
)

# Modules whose public callables are infrastructure, not analytics.
# The curve-construction layer is deliberately here: a Curve's identity
# travels as its config content hash (I4) stamped into every envelope that
# consumes it, rather than as an envelope of its own. Anything that *returns
# a number a user sees* stays outside this list and must carry @model.
_INFRASTRUCTURE_MODULES = {
    "treble.analytics.registry",
    "treble.analytics._ql",
    "treble.analytics.curves.config",
    "treble.analytics.curves.bootstrap",
    "treble.analytics.curves.interpolators",
    "treble.analytics.curves.hagan_west",
}


def _walk_public_callables() -> list[tuple[str, str, object]]:
    found: list[tuple[str, str, object]] = []
    for info in pkgutil.walk_packages(treble.analytics.__path__, prefix="treble.analytics."):
        if info.name in _INFRASTRUCTURE_MODULES or info.name.rsplit(".", 1)[-1].startswith("_"):
            continue
        module = importlib.import_module(info.name)
        for name, obj in vars(module).items():
            if name.startswith("_") or not callable(obj):
                continue
            if inspect.isclass(obj) or getattr(obj, "__module__", None) != info.name:
                continue
            found.append((info.name, name, obj))
    return found


def test_all_public_analytics_are_registered() -> None:
    unregistered = [
        f"{module}.{name}"
        for module, name, obj in _walk_public_callables()
        if not is_registered_model(obj)
    ]
    assert unregistered == [], "public analytics callables without @model (I3): " + ", ".join(
        unregistered
    )


class TestEnvelope:
    def test_decorated_function_returns_complete_envelope(self) -> None:
        @model(model_id="test.add", version="1.0", spec_section="§test")
        def add(a: float, b: float = 1.0) -> float:
            return a + b

        result = add(2.0)
        assert isinstance(result, ModelResult)
        assert result.value == 3.0
        assert result.model_id == "test.add"
        assert result.model_version == "1.0"
        assert result.parameters == {"a": "2.0", "b": "1.0"}
        assert result.computed_at.tzinfo is not None
        del MODEL_REGISTRY["test.add"]

    def test_content_hashed_inputs_are_captured(self) -> None:
        class FakeCurveConfig:
            content_hash = "cafebabe"

        @model(model_id="test.curve_user", version="1.0", spec_section="§test")
        def price(config: FakeCurveConfig) -> float:
            return 100.0

        result = price(FakeCurveConfig())
        assert result.inputs == {"config": "cafebabe"}
        assert result.parameters["config"] == "<FakeCurveConfig:cafebabe>"
        del MODEL_REGISTRY["test.curve_user"]

    def test_duplicate_model_id_rejected(self) -> None:
        @model(model_id="test.dup", version="1.0", spec_section="§test")
        def first() -> int:
            return 1

        with pytest.raises(ValueError, match="already registered"):

            @model(model_id="test.dup", version="1.0", spec_section="§test")
            def second() -> int:
                return 2

        del MODEL_REGISTRY["test.dup"]

    def test_registration_visible_to_mdl(self) -> None:
        @model(model_id="test.mdl", version="2.1", spec_section="§10.1", summary="demo")
        def analytic() -> int:
            return 0

        entry = MODEL_REGISTRY["test.mdl"]
        assert entry.meta.version == "2.1"
        assert entry.meta.spec_section == "§10.1"
        assert entry.qualname.endswith("analytic")
        del MODEL_REGISTRY["test.mdl"]
