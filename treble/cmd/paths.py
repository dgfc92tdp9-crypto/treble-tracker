"""Where the workstation keeps its data.

Extracted from `cli.py` so the TUI can name the same directory without
importing the CLI, which imports the TUI. Nothing here imports anything of
ours, so it cannot participate in a cycle.
"""

from __future__ import annotations

import os
from pathlib import Path


def default_data_dir() -> Path:
    """Where the workstation looks for its store, independent of cwd.

    This was a relative ``Path("data")``, so which store opened depended on
    the directory the command ran from. Launched from the Dock, or from a
    terminal anywhere but the repo root, it silently created a fresh empty
    store and rendered a screen of honest-looking dashes with no error —
    the exact shape of a wrong display that never announces itself.

    ``TREBLE_DATA_DIR`` overrides. Otherwise the repo's own ``data/`` is
    used when this is a source checkout, falling back to ``~/.treble`` for
    an installed copy that has no repo to anchor to.
    """
    override = os.environ.get("TREBLE_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    repo_root = Path(__file__).resolve().parents[2]
    if (repo_root / "pyproject.toml").is_file():
        return repo_root / "data"
    return Path.home() / ".treble"


DEFAULT_DATA_DIR = default_data_dir()

__all__ = ["DEFAULT_DATA_DIR", "default_data_dir"]
