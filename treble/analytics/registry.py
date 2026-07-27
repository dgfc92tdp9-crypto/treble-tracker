"""The @model decorator and MDL backing store — invariant I3 (CLAUDE.md §1).

No analytic returns a bare number. Every public analytics function is
wrapped by :func:`model`, which registers it at import time (the `MDL`
screen renders this registry) and wraps its return value in a
:class:`ModelResult` envelope carrying the model identity, the full
parameter set, input snapshot references, and the computation timestamp.

Inputs with a ``content_hash`` attribute (curve configurations — I4) are
recorded in the envelope's ``inputs`` automatically, so curve identity
propagates without each analytic remembering to do it.

A static CI check walks ``treble.analytics`` and fails on any public
callable without ``__model_meta__``.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ModelMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_id: str
    version: str
    spec_section: str  # the spec section this model implements, e.g. "§10.2"
    summary: str


class ModelResult[T](BaseModel):
    """The envelope: value plus everything needed to reproduce it."""

    model_config = ConfigDict(frozen=True)

    value: T
    model_id: str
    model_version: str
    parameters: dict[str, str]
    inputs: dict[str, str]  # snapshot references: curve config hashes, data as-of stamps
    computed_at: datetime


class RegisteredModel(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    meta: ModelMeta
    qualname: str
    module: str


MODEL_REGISTRY: dict[str, RegisteredModel] = {}


def load_all_models() -> dict[str, RegisteredModel]:
    """Import every analytics submodule, then return the registry.

    Models register as a side effect of `@model` at import time, so reading
    `MODEL_REGISTRY` without importing anything returns ``{}`` — and `MDL`
    would render an empty table that reads as "this system has no models"
    rather than "nothing has been imported yet". That is the silent-wrong-
    display failure this project exists to avoid, so discovery is explicit
    and exhaustive rather than relying on some other module having happened
    to import the right things first.
    """
    import importlib
    import pkgutil

    import treble.analytics

    for info in pkgutil.walk_packages(
        treble.analytics.__path__, prefix=f"{treble.analytics.__name__}."
    ):
        importlib.import_module(info.name)
    return MODEL_REGISTRY


def _stringify(value: object) -> str:
    if isinstance(value, float | int | bool | str) or value is None:
        return repr(value)
    content_hash = getattr(value, "content_hash", None)
    if content_hash is not None:
        return f"<{type(value).__name__}:{content_hash}>"
    return f"<{type(value).__name__}>"


def model[**P, T](
    *, model_id: str, version: str, spec_section: str, summary: str = ""
) -> Callable[[Callable[P, T]], Callable[P, ModelResult[T]]]:
    """Register an analytic and wrap its return in the I3 envelope."""

    meta = ModelMeta(model_id=model_id, version=version, spec_section=spec_section, summary=summary)

    def decorate(fn: Callable[P, T]) -> Callable[P, ModelResult[T]]:
        if model_id in MODEL_REGISTRY:
            registered = MODEL_REGISTRY[model_id]
            raise ValueError(f"model id {model_id!r} already registered by {registered.qualname}")
        MODEL_REGISTRY[model_id] = RegisteredModel(
            meta=meta, qualname=fn.__qualname__, module=fn.__module__
        )
        signature = inspect.signature(fn)

        def wrapper(*args: P.args, **kwargs: P.kwargs) -> ModelResult[T]:
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            parameters: dict[str, str] = {}
            inputs: dict[str, str] = {}
            for name, value in bound.arguments.items():
                content_hash = getattr(value, "content_hash", None)
                if content_hash is not None:
                    inputs[name] = str(content_hash)
                parameters[name] = _stringify(value)
            value = fn(*args, **kwargs)
            return ModelResult(
                value=value,
                model_id=model_id,
                model_version=version,
                parameters=parameters,
                inputs=inputs,
                computed_at=datetime.now(UTC),
            )

        wrapper.__name__ = fn.__name__
        wrapper.__qualname__ = fn.__qualname__
        wrapper.__doc__ = fn.__doc__
        wrapper.__module__ = fn.__module__
        wrapper.__model_meta__ = meta  # type: ignore[attr-defined]
        wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
        return wrapper

    return decorate


def is_registered_model(obj: Any) -> bool:
    return getattr(obj, "__model_meta__", None) is not None
