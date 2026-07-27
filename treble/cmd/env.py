"""Load `.env` for the local install (spec §23.3 local-only mode).

Credentials live in a gitignored `.env` (FINRA, EDGAR contact, OpenFIGI).
Without this, the file exists and the application ignores it — which is
exactly the failure it produced: `treble tui` refused to start while valid
credentials sat unread on disk.

Deliberately a small local reader rather than a dependency: CLAUDE.md §2
fixes the stack and says not to substitute without asking, and this is
fifteen lines that can be audited at a glance.

Rules, in the order that matters:

- **A real environment variable always wins.** `.env` is a convenience for
  the local install, never an override of what the operator set explicitly.
- **Values are never logged.** Nothing here prints or returns the values;
  only the count of keys applied, so a misconfigured file is diagnosable
  without leaking a secret into a terminal or CI log.
"""

from __future__ import annotations

import os
from pathlib import Path


def parse_env_file(text: str) -> dict[str, str]:
    """Parse `.env` text into key/value pairs.

    Pure and value-blind: comments, blank lines and malformed lines are
    skipped rather than raising, because a stray line should not stop the
    workstation from opening.
    """
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def load_env(start: Path | None = None) -> int:
    """Apply `.env` from ``start`` or the nearest parent, without clobbering.

    Returns the number of keys applied — never the keys' values.
    """
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            applied = 0
            for key, value in parse_env_file(candidate.read_text()).items():
                # An explicitly-set environment variable is authoritative.
                if key not in os.environ:
                    os.environ[key] = value
                    applied += 1
            return applied
    return 0
