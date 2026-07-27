"""WP0 toolchain smoke test: the pinned stack imports on this platform.

CLAUDE.md §2 fixes the stack; if any of these fail to import, nothing else
in the suite is meaningful.
"""

import importlib

import pytest

CORE_DEPENDENCIES = [
    "QuantLib",
    "duckdb",
    "polars",
    "pyarrow",
    "pydantic",
    "lark",
    "hypothesis",
    "textual",
    "fastapi",
]


@pytest.mark.parametrize("module", CORE_DEPENDENCIES)
def test_core_dependency_imports(module: str) -> None:
    importlib.import_module(module)


def test_treble_package_imports() -> None:
    import treble  # noqa: F401
