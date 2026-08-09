"""Where the workstation finds its data.

The default was a relative ``Path("data")``, so the store that opened
depended on the directory the command was launched from. From the Dock,
or from a terminal anywhere but the repo root, it created a fresh empty
store and rendered a screen of dashes with no error at all — a wrong
display that never announced itself. These pin the fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from treble.cmd.cli import DEFAULT_CONFIG, DEFAULT_DATA_DIR
from treble.cmd.paths import default_data_dir as _default_data_dir


class TestDataDirIsIndependentOfCwd:
    def test_default_is_absolute(self) -> None:
        assert DEFAULT_DATA_DIR.is_absolute()

    def test_config_default_is_absolute(self) -> None:
        # Same failure mode: a relative config path resolves differently
        # depending on where the command was run from.
        assert DEFAULT_CONFIG.is_absolute()

    def test_same_answer_from_any_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TREBLE_DATA_DIR", raising=False)
        here = _default_data_dir()
        monkeypatch.chdir(tmp_path)
        assert _default_data_dir() == here

    def test_environment_overrides(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TREBLE_DATA_DIR", str(tmp_path))
        assert _default_data_dir() == tmp_path.resolve()

    def test_source_checkout_uses_the_repo_store(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TREBLE_DATA_DIR", raising=False)
        repo_root = Path(__file__).resolve().parents[2]
        assert _default_data_dir() == repo_root / "data"
