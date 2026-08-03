"""Every shipped adapter must have an offline fixture test.

CLAUDE.md §7 requires each adapter to be tested against a recorded payload.
Two adapters — ECB and Coinbase — shipped without one, and nothing failed:
the drift check verifies an adapter has *run*, not that it is covered, and
the coverage floor is satisfied by any test that imports the module.

So the requirement existed and nothing enforced it. That is the shape this
project keeps finding, and this is the enforcement.

Unlike the drift check, this runs in CI: it reads the repository, not the
live install.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest

import treble.ingest
from treble.ingest.base import SourceAdapter

TESTS = Path(__file__).parent


def shipped_adapters() -> list[str]:
    """Modules that define a `SourceAdapter`, discovered rather than listed.

    The first version excluded non-adapters by a hand-maintained filename
    set — `{"__init__", "base", "populate"}` — and `treble/ingest/registry.py`
    duly failed the check the day it was added, for defining no adapter and
    parsing no payload.

    Adding a fourth name to that set would have been the convenient fix and
    the wrong one: it is the same move that once "fixed" a drift check by
    deleting the two adapters it was failing on. So the rule now tests the
    property it cares about — a module is an adapter module when it defines
    a `SourceAdapter` subclass — and the list maintains itself. A real
    adapter added tomorrow is checked because it is an adapter, not because
    somebody remembered to leave its name out of an exclusion set.
    """
    found: list[str] = []
    for info in pkgutil.walk_packages(treble.ingest.__path__, prefix="treble.ingest."):
        module = importlib.import_module(info.name)
        defines_adapter = any(
            issubclass(obj, SourceAdapter) and obj is not SourceAdapter
            for _, obj in inspect.getmembers(module, inspect.isclass)
            if obj.__module__ == info.name
        )
        if defines_adapter:
            found.append(info.name.rsplit(".", 1)[-1])
    return sorted(found)


def test_there_are_adapters_to_check() -> None:
    """Guards the check below: an empty list would pass vacuously."""
    assert len(shipped_adapters()) >= 8


def _modules_covering(adapter: str) -> list[Path]:
    """Test modules that import the adapter, by any filename.

    Searched by content rather than by a `test_<module>.py` convention: the
    first draft of this check asserted the filename and reported `gleif` and
    `openfigi` as uncovered when both are tested in `test_openfigi_gleif.py`.
    A check that enforces a naming habit instead of the property it cares
    about produces false alarms, and false alarms are how a real one gets
    ignored.
    """
    needle = f"treble.ingest.{adapter}"
    return [path for path in TESTS.glob("test_*.py") if needle in path.read_text()]


@pytest.mark.parametrize("adapter", shipped_adapters())
def test_each_adapter_is_covered(adapter: str) -> None:
    assert _modules_covering(adapter), (
        f"no test module imports treble.ingest.{adapter}. CLAUDE.md §7 requires an "
        "offline recorded-fixture test for every adapter; without one it can ship, "
        "and even run in production, with nothing checking what it parses."
    )


@pytest.mark.parametrize("adapter", shipped_adapters())
def test_each_adapter_test_uses_a_recorded_payload(adapter: str) -> None:
    """A test that never opens a fixture is testing its own mocks — and a
    mocked adapter passes exactly as well as a real one."""
    covering = _modules_covering(adapter)
    if not covering:
        pytest.skip("covered by the previous check")
    assert any("fixtures" in path.read_text() for path in covering), (
        f"{adapter} is imported by tests but none of them read a recorded payload"
    )
