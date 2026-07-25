# PROGRESS

Live build state. **Update at the end of every session.** Write it for a reader who remembers nothing — after a context reset, that reader is you.

Do not restate the spec or `CLAUDE.md` here. This file holds only: where we are, what is blocked, and what was decided.

---

## Current position

**Phase:** 1 — research workstation
**Status:** Phase 0 complete; plan approved by Jack 2026-07-25. Work packages WP0–WP15 defined
(see approved plan, mirrored by the decision records). Starting WP0 (toolchain + CI).
**Next action:** WP0 — verify `make setup` on Apple Silicon, add GitHub Actions workflow,
create private remote, first commit. Then WP1 (core) → WP2 (store) → WP3 (screen contract)
→ WP4/5 (analytics) → WP6/7 (ingest, master) → WP8–10 (TAPI, TQL, cmd) → WP11/12
(screens, renderers) → WP13–15 (spreadsheet, local-only, gate audit).

---

## Phase 0 — planning

- [x] Specification read in full
- [x] `CLAUDE.md` read
- [x] Enforcement mechanism designed for each of the seven invariants (I1–I7), each with a test that fails if the mechanism is removed *(mechanisms + kill-tests in the approved plan; ADR-0001 covers the I2 storage shape)*
- [x] Screen definition contract designed: schema, resolver interface, conformance approach (I6)
- [x] Phase 1 task breakdown mapped onto the scaffolded package layout *(WP0–WP15)*
- [x] Open questions raised and resolved *(three blocking questions answered by Jack — see Decisions; four non-blocking defaults recorded under Open questions)*
- [x] **Plan approved by Jack** — 2026-07-25

---

## Phase 1 — research workstation

Criteria are copied from `CLAUDE.md` §8. Tick only when passing in CI on a clean checkout.

- [ ] All seven invariants have enforcement mechanisms, each with a test that fails if the mechanism is removed
- [ ] Screen definition schema, resolver contract, and conformance suite exist; both renderers pass it
- [ ] Command grammar parses every example in spec §5.1 plus a fuzz corpus; yellow-key namespace resolution correct
- [ ] Ingest adapters: EDGAR, FRED, Treasury, TRACE-file, OpenFIGI, GLEIF — each with offline fixture tests
- [ ] Security master and entity graph populated for the configured universe subsets
- [ ] Screens working in both clients: `DES` `FA` `GP` `HP` `YAS` `ICVS` `SRCH` `EQS` `FLDS` `SPTR` `MDL`
- [ ] Curve bootstrapping reprices inputs to 1e-10 across all supported interpolation methods
- [ ] `YAS` golden-value tests passing against published references
- [ ] TAPI with Python client; `TDP`/`TDH`/`TDS` spreadsheet functions via xlwings
- [ ] TQL parses and executes the spec §4.2 example
- [ ] Local-only mode: one command from clean checkout to working workstation
- [ ] `PROGRESS.md` current

---

## Phases 2–5

Not started. Criteria in `CLAUDE.md` §8. Do not begin a phase until the previous one is fully green.

---

## Open questions

*Blocking questions first. Remove once answered, and record the answer as a decision if it was architectural.*

Non-blocking, proceeding on stated defaults (flag if wrong):

- **Spreadsheet add-in host** — xlwings requires Excel; if not installed on this Mac, WP13 targets LibreOffice Calc instead. Default: build on xlwings, verify against whichever is present.
- **`FA` standardisation depth in Phase 1** — as-reported XBRL in full + core standardised statement set; unmapped extension tags surfaced, never dropped. Full global chart of accounts is Phase 2 scale.
- **`GP`/`HP` are EOD/historical only in Phase 1** (no ticker plant until Phase 2); blink/stale semantics built into the contract now.
- **Spec nit** — §23.3 Phase 1 lists `HP` but the §7 function tables omit it (it appears in §5.2 and the glossary). Treating `HP` as the historical price table, flagged here per the no-invented-mnemonics rule.

---

## Decisions

*One line each, linking to the full record in `docs/decisions/`. Do not duplicate the record's content here.*

- [0001](docs/decisions/0001-bitemporal-immutable-rows.md) — Bitemporal rows immutable; `knowledge_to` derived at query time (I2)
- [0002](docs/decisions/0002-hagan-west-in-repo.md) — Hagan–West monotone convex implemented in-repo; QuantLib lacks it
- [0003](docs/decisions/0003-phase1-oas-user-vol.md) — Phase 1 OAS: HW lattice with explicit user-supplied vol; VCUB is a Phase 2 drop-in
- [0004](docs/decisions/0004-ci-github-actions.md) — CI = GitHub Actions on a private remote; `make check` is the single gate locally and in CI
- [0005](docs/decisions/0005-phase1-universe-all-edgar-filers.md) — Phase 1 default universe = all EDGAR filers; bulk-first resumable ingest; CI fixture-only

---

## Known deviations from the spec

*Anything built differently from what the spec says, with the reason and the authorising decision record. Empty is the goal.*

- Single-machine substitutions per `CLAUDE.md` §2 and §3 — authorised, interfaces preserved

---

## Session log

*Newest first. Two or three lines each: what was done, what broke, what is next.*

### 2026-07-25 — Phase 0 complete, Phase 1 started
Read spec, CLAUDE.md, PROGRESS.md in full. Produced the Phase 0 plan (invariant mechanisms +
kill-tests, screen contract, WP0–WP15 breakdown, open questions); Jack answered the three
blocking questions (CI = GitHub Actions; universe = all EDGAR filers; OAS = lattice with user
vol) and approved. Wrote ADR-0001..0005. Next: WP0 toolchain + CI, then WP1 core.

### (not yet started)
Repository scaffolded with layout, tooling, invariant enforcement config, and the four orientation
documents. Awaiting first working session.
