#!/usr/bin/env bash
# The single verification gate. Run this before every commit.
#
# Exists because the same mistake was made three times on 2026-07-27:
# piping a check into `tail` so its exit code was masked, reading the
# output as if it were a pass, and committing a failure. `set -euo
# pipefail` makes that specific error impossible — a failing stage stops
# the script whether or not anything is piped.
#
# Any stage that fails prints its own output and the script exits non-zero.
set -euo pipefail

VENV="$(dirname "$0")/../.venv/bin"
cd "$(dirname "$0")/.."

stage() {
  printf '\n=== %s ===\n' "$1"
  shift
  "$@"
}

stage "lint + security"   "$VENV/ruff" check .
stage "format"            "$VENV/ruff" format --check .
stage "types (strict)"    "$VENV/mypy" treble
stage "architecture (I7)" "$VENV/lint-imports"
stage "web renderer"      ./scripts/build_web.sh
stage "tests + coverage"  "$VENV/pytest" -q
# After pytest, so it reads the data this run produced rather than the last.
stage "module coverage"    "$VENV/python" scripts/check_module_coverage.py
# The class of defect that manual sweeps kept finding and nothing failed on.
stage "unread members"    "$VENV/python" scripts/check_unread_members.py
# Tested and never called: what the two gates above cannot see.
stage "reachability"      "$VENV/python" scripts/check_unreachable_modules.py
# The working copy rather than the code: a correct compaction nobody runs.
stage "storage budget"    "$VENV/python" scripts/check_storage_budget.py

printf '\nGATE GREEN — safe to commit\n'
