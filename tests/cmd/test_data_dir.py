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
from treble.cmd.paths import configured_data_dir
from treble.cmd.paths import default_data_dir as _default_data_dir
from treble.core.datadir import POINTER_NAME


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

    def test_source_checkout_starts_at_the_repo_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A source checkout anchors to its own ``data/``.

        This asserted the same thing of `default_data_dir()` and **went red
        the first time the store was actually relocated** (2026-10-02, onto
        an external disk). That was the test being wrong, not the move:
        `default_data_dir()` follows relocation pointers by contract — "no
        variable to export and none to forget" is the whole point of it —
        so once `data/` holds a pointer it correctly answers with the
        external path.

        Where a checkout *anchors* is `configured_data_dir()`'s question,
        and that is the property this test's name describes. The pointer
        half is pinned separately below.

        Worth recording why nobody caught it: the relocation tooling was
        proven against a real APFS sparse image, but the suite was never
        run on a machine whose store had *been* relocated. The mechanism
        was tested; its interaction with everything else was not.
        """
        monkeypatch.delenv("TREBLE_DATA_DIR", raising=False)
        repo_root = Path(__file__).resolve().parents[2]
        assert configured_data_dir() == repo_root / "data"

    def test_a_relocated_store_is_followed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The other half, and previously pinned nowhere at this level: the
        # override is applied first, then the pointer from wherever it lands.
        start, moved = tmp_path / "start", tmp_path / "moved"
        start.mkdir()
        moved.mkdir()
        (start / POINTER_NAME).write_text(f'{{"moved_to": "{moved}"}}')
        monkeypatch.setenv("TREBLE_DATA_DIR", str(start))
        assert configured_data_dir() == start.resolve()
        assert _default_data_dir() == moved
