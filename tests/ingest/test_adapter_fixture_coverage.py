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

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADAPTERS = ROOT / "treble" / "ingest"
TESTS = Path(__file__).parent

#: Modules in `treble/ingest` that are not adapters.
_NOT_ADAPTERS = {"__init__", "base", "populate"}


def shipped_adapters() -> list[str]:
    return sorted(
        path.stem
        for path in ADAPTERS.glob("*.py")
        if path.stem not in _NOT_ADAPTERS and not path.stem.startswith("_")
    )


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
