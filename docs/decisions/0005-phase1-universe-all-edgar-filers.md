# 0005 - Phase 1 default universe: all EDGAR filers

2026-07-25 | Status: accepted (Jack, 2026-07-25)

## Context

The Phase 1 gate requires the security master and entity graph "populated for the configured
universe subsets" (CLAUDE.md §8). The scale of that universe determines ingest design.
Options considered: a tiny dev universe, S&P 500 + their bonds, or all EDGAR filers. Jack
chose all filers.

## Decision

The default configured universe is **every EDGAR XBRL filer** (~8k+ companies), their
TRACE-file-covered USD corporate bonds, all outstanding US Treasuries, and a core FRED macro
set. Universe definitions remain a config file (`config/universe.yaml`), so subsets are a
configuration, not a code change.

Consequences for ingest design (binding on WP6/WP7):

- **Bulk-first is mandatory** — `companyfacts.zip`, `submissions.zip`, quarterly Financial
  Statement Data Sets, GLEIF concatenated files. Per-company crawling at this scale is slow
  and abusive of the free sources (CLAUDE.md §6).
- **Resumable, out-of-band ingest** — `treble ingest` runs incrementally with checkpoints in
  the ingest log; the initial load takes hours and tens of GB and must survive interruption.
- **CI never ingests** — recorded fixtures only; the full load runs on the workstation.
- Seed fixtures ship in-repo so `treble init` produces a working workstation before any full
  ingest has run (local-only mode criterion).

## Consequences

- Easy: `SRCH`/`EQS` screens are real at full breadth; gate criteria are exercised at
  production scale from day one.
- Hard: initial ingest wall-clock and disk (est. 20–50 GB with Parquet compression) on one
  MacBook; adapters must respect rate limits strictly.
- Forecloses: nothing — smaller universes remain a config choice.
