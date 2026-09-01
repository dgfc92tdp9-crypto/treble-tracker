"""Where the workstation keeps its data.

Extracted from `cli.py` so the TUI can name the same directory without
importing the CLI, which imports the TUI. The only thing it imports of ours
is `store.identity`, which imports nothing of ours in turn — so this still
cannot participate in a cycle.
"""

from __future__ import annotations

import os
from pathlib import Path

from treble.core.datadir import resolve


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

    A **relocation pointer** is then followed from wherever that lands
    (`store.identity`). Moving the store onto another disk therefore needs
    no configuration at all: no variable to export and none to forget,
    which matters because forgetting one recreates the empty-store failure
    this function was written to end — the bytes on the external disk, the
    default path pointing at an empty directory, and a new store quietly
    built in it.

    The override is applied *before* the pointer so an operator can always
    name a directory directly and be obeyed.
    """
    override = os.environ.get("TREBLE_DATA_DIR")
    if override:
        return resolve(Path(override).expanduser().resolve())
    repo_root = Path(__file__).resolve().parents[2]
    if (repo_root / "pyproject.toml").is_file():
        return resolve(repo_root / "data")
    return resolve(Path.home() / ".treble")


def configured_data_dir() -> Path:
    """Where the resolver *starts*, before following any pointer.

    The guard needs both ends: the answer alone cannot say "the store was
    moved to X and X is absent", only "X is absent", which is the
    difference between a fixable message and a puzzle.
    """
    override = os.environ.get("TREBLE_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    repo_root = Path(__file__).resolve().parents[2]
    if (repo_root / "pyproject.toml").is_file():
        return repo_root / "data"
    return Path.home() / ".treble"


DEFAULT_DATA_DIR = default_data_dir()

__all__ = ["DEFAULT_DATA_DIR", "configured_data_dir", "default_data_dir"]
