# 0004 - CI is GitHub Actions on a private remote; `make check` is the single gate

2026-07-25 | Status: accepted (Jack, 2026-07-25)

## Context

CLAUDE.md §8 requires every phase-gate criterion to pass "in CI, on a clean checkout", but
the repository was local-only git with no remote and no CI definition.

## Decision

Create a private GitHub repository as the remote. CI is GitHub Actions running exactly
`make check` (ruff lint + format check, `mypy --strict`, `lint-imports`, pytest) on every
push, on a macOS ARM64 runner matching the development machine plus an ubuntu runner for
portability. `make check` remains the identical local command, so local green and CI green
mean the same thing. Phase gates are audited against a green CI run on a fresh clone.
No network access in CI — all ingest tests use recorded fixtures (CLAUDE.md §7).

## Consequences

- Easy: gate criteria have an unambiguous, auditable meaning; the local/CI parity rule
  prevents "works on my machine" drift.
- Hard: keeping CI green on both macOS and Linux runners means platform-conditional
  behaviour (QuantLib wheels, xlwings) must be marked/skipped explicitly rather than assumed.
- Forecloses: nothing; a different CI host later only re-points the same `make check`.
