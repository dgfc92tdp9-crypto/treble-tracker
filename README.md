# Treble Tracker

A free and open institutional finance workstation. macOS (Apple Silicon), built with Claude Code.

---

## For Claude Code — read in this order

| # | File | What it is |
|---|---|---|
| 1 | `START_HERE.md` | Your task. Read it first; it tells you what to do before writing any code. |
| 2 | `docs/treble-tracker-spec.md` | **The contract.** ~20,000 words. What to build. Read it in full. |
| 3 | `CLAUDE.md` | **How to build it.** Invariants, stack, layout, gotchas, phase gates. Re-read every session. |
| 4 | `PROGRESS.md` | **Where we are.** Live state. Update at the end of every session. |

### Three sources of truth, no overlap

Keeping these separate is deliberate. Duplicated instructions drift and then contradict each other, and a contradiction between two files you both trust is worse than a gap in one.

- **The spec** says *what* the system does. Function mnemonics, field names, models, behaviours.
- **`CLAUDE.md`** says *how* to build it. Never restates the spec; only tells you how to satisfy it.
- **`PROGRESS.md`** says *where you are*. Never restates either; only tracks state and decisions.

If two files disagree, the precedence is **spec → CLAUDE.md → PROGRESS.md → anything else**. If the disagreement is real rather than an oversight, stop and raise it.

Architectural decisions go in `docs/decisions/` as numbered records, not into these four files.

---

## The one thing to get right first

The spec (§6.1) requires that **one declarative screen definition drives all three renderers** — Tauri desktop, Textual TUI, and web. This is invariant **I6** and it is the one thing in this build that cannot be retrofitted.

Build the screen definition schema and its renderer conformance suite **before** either renderer. Details in `START_HERE.md`.

---

## Layout

```
CLAUDE.md              how to build          <- read every session
START_HERE.md          the task
PROGRESS.md            live state            <- update every session
pyproject.toml         stack, pinned from CLAUDE.md section 2
.importlinter          enforces I7 (TAPI is the only data path)
Makefile               setup / lint / types / test / conformance / check

docs/
  treble-tracker-spec.md    the contract
  decisions/                numbered architectural decision records

treble/
  core/          identifiers, security master, entity graph, provenance, bitemporal facts
  ingest/        source adapters, one per source
  store/         storage protocols + DuckDB/Parquet implementations
  analytics/     curves, bonds, mortgage, credit, equity, vol, derivatives, risk, tval
  tql/           query language
  tapi/          the API - the only data path
  screens/       screen definitions + resolvers
  render/        contract/ (build first), tui/, web/
  cmd/           command grammar
  ai/            phase 5

tests/
  golden/        published reference values
  fixtures/      recorded source payloads, no network in CI
  conformance/   renderer conformance cases
```

Every `__init__.py` names the spec section its package implements.

---

## Setup

```bash
make setup     # uv venv + install + pre-commit hooks
make check     # lint, types, import contracts, tests - what CI runs
```

Requires: Python 3.12+, [`uv`](https://docs.astral.sh/uv/), Rust (Tauri), Node (Tauri).
Docker is not needed until Phase 3.

---

## Status

See `PROGRESS.md`. Phases are gated: **no phase N+1 until every criterion of phase N passes in CI on a clean checkout.**
