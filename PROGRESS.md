# PROGRESS

Live build state. **Update at the end of every session.** Write it for a reader who remembers nothing — after a context reset, that reader is you.

Do not restate the spec or `CLAUDE.md` here. This file holds only: where we are, what is blocked, and what was decided.

---

## Current position

> **Repo location: `~/dev/treble-tracker`.** Moved out of `~/Documents` on 2026-07-27 because
> that folder is iCloud-synced: every temp file, DuckDB database and fixture read went through
> the sync layer, making the suite effectively unrunnable (ten-minute cycles, `git index.lock`
> write timeouts). After the move the full suite runs in 10s. **Do not move it back under
> `~/Documents`, `~/Desktop`, or any iCloud-synced path.** GitHub is the backup now.

**Phase:** 1 — research workstation
**Status:** WP0–WP6 complete (EDGAR/FRED/Treasury/OpenFIGI/GLEIF/N-PORT/TRACE-aggregates, all
with real recorded fixtures — TRACE per-trade is a settled non-goal, see Data access findings).
WP7 in progress: instrument identity resolution (FIGI hierarchy, `core/master.py`) and the
entity graph's primary source — GLEIF Level 2 Relationship Records, parent/subsidiary + fund
structure — are both built and tested (`core/entity_graph.py`,
`ingest/gleif.GleifRelationshipAdapter`). EDGAR Exhibit 21 and OpenCorporates (spec §9.5's
other two entity-graph sources) are not yet built. Security-master *population* for the full
configured universe (~8k EDGAR filers, decision 0005) has not been run — no
`config/universe.yaml` or resumable population runner exists yet; this is the next real gap
against the Phase 1 checklist item, not just a data-loading step.
Full local suite (offline, no drift): 193 tests + 18 new this session = 211, all green.
Coverage floor recalibrated 80% → 84% (measured 84.75%) after running the full suite for the
first time this session — the 80% figure had gone stale.
Completion vs the fixed model: not recomputed this session — the memory record describing
that model was expected in the persistent memory store but is not there (memory dir is empty).
Flagging so the ~13.13% figure from the last session log entry is not treated as current;
recompute once the model is confirmed to still exist and is re-saved.
**Next action:** commit this session's WP7 entity-graph work once mypy --strict confirms clean
on the two new files (running now) and CI is green; then either (a) EDGAR Exhibit 21 /
OpenCorporates to complete §9.5's entity-graph sources, or (b) the `config/universe.yaml` +
resumable population runner to actually populate the security master, or (c) resume the
vertical slice (WP8 TAPI → WP10 cmd → WP11 DES/YAS → WP12 TUI → WP14 local-only) — Jack to
confirm priority among these three before the next one is started, since (b) in particular is
an architectural decision (checkpointing strategy, OpenFIGI's severe unauthenticated rate
limit against ~8k+ securities) rather than a small addition.
**Standing directives (Jack):** accuracy above all; stress tests + real data always; API
choices delegated (pick accuracy-maximising, report after); launch = full spec through
Phase 5; zero external cost (ubuntu-only CI, no cloud routines; pause on token exhaustion).

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
- [ ] Pane form selection enforced at load time; CI validates every `.screen.yaml` against
      the screen-definition contract (spec §6.1 — requested by Jack citing §6.4; §6.4 is
      currently the charting engine, reconcile if the spec is revised)
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

- **Release definition (Jack, 2026-07-25):** "launch" = the complete spec through Phase 5
  (real-time, PORT/TFM3, messaging, execution, AI) — no public/phased launch before that.
  Build order and phase gates unchanged; deadline explicitly subordinate to completeness.

- [0001](docs/decisions/0001-bitemporal-immutable-rows.md) — Bitemporal rows immutable; `knowledge_to` derived at query time (I2)
- [0002](docs/decisions/0002-hagan-west-in-repo.md) — Hagan–West monotone convex implemented in-repo; QuantLib lacks it
- [0003](docs/decisions/0003-phase1-oas-user-vol.md) — Phase 1 OAS: HW lattice with explicit user-supplied vol; VCUB is a Phase 2 drop-in
- [0004](docs/decisions/0004-ci-github-actions.md) — CI = GitHub Actions on a private remote; `make check` is the single gate locally and in CI
- [0005](docs/decisions/0005-phase1-universe-all-edgar-filers.md) — Phase 1 default universe = all EDGAR filers; bulk-first resumable ingest; CI fixture-only

---

## Continuous verification (standing requirement, Jack 2026-07-26)

"Make sure holes are always found, even after their creation." Write-time checks are not
enough — these six must exist and stay in place. **Never remove or weaken them; when adding
an external source, add its fixture-drift check at the same time.**

- [x] **Scheduled deep CI run** — `.github/workflows/deep.yml`, nightly 03:17 UTC, Hypothesis
      `deep` profile at 2000 examples/property (`make deep` locally). This is how the
      Hagan–West quadrature blind spot was found
- [x] **Persistent Hypothesis example database** — `.hypothesis/examples` cached across deep
      runs; any counterexample ever found replays forever, so a fixed bug cannot regress
- [x] **Fixture-drift detection** — `tests/ingest/test_fixture_drift.py`, marker `drift`,
      gated on `TREBLE_CHECK_DRIFT=1` so the offline contract holds for the normal suite.
      Compares live *schema* (not values) against every recorded fixture across all seven
      feeds. **When adding a source, add its drift check in the same commit.**
- [x] **Coverage floor in CI** — `--cov-fail-under=84` in pytest addopts (measured 89.42%);
      untested new code cannot land (would have caught the renamed-but-unexercised
      `TraceCredentialsMissing` call sites)
- [ ] **Mutation testing** — `make mutate`, config in `[tool.mutmut]`. **Configured but never
      completed a run.** mutmut 3.x copies only `paths_to_mutate` into its sandbox, so a
      scoped config breaks on cross-package test imports; now set to the whole of `treble/`
      with `tests/`, which is correct but a long job. Next session: run it to completion,
      record the kill rate, and investigate any surviving mutants — a survivor means a test
      that passes whether or not the code is right. `mutants/` is gitignored (it was
      committed by mistake once and removed in a2bd7f7).
- [x] **`pip-audit` in CI** — dependency vulnerabilities disclosed after shipping

**Full battery run 2026-07-27 from the new location, all green except mutation:** 211 tests,
coverage 89.42%, mypy --strict over 52 files, both import contracts kept, ruff+bandit clean,
no dependency vulnerabilities, deep stress at 2000 examples/property with no counterexamples,
live schema drift checked against all seven feeds with no drift. Suite runtime 10s (51s with
coverage). Three real defects found by this battery: eight late-binding closures in the ingest
adapters (silent cross-instrument corruption on any future refactor), three property tests
whose hardcoded `max_examples` silently overrode the deep profile (so the nightly run was
never stressing price↔yield, the duration identity, or the I2 guarantee), and a `make mutate`
target that had never worked.

## Data access findings (settled 2026-07-26 — do not re-derive)

Probed with a real FINRA API account (Jack's, credentials in gitignored `.env`):

- **FINRA `fixedIncomeMarket/treasuryDailyAggregates`** — works with credentials. Parsed and
  fixture-tested. Aggregates only: ATS/dealer counts, volumes, VWAP by product and maturity.
- **FINRA `fixedIncomeMarket/trace`** (individual corporate transactions) — **404 with a valid
  token** on GET and POST. Entitlement-gated; FINRA sells it (TRACE Data Feeds / End-Of-Day
  Transaction File / Enhanced Historical). Not available free.
- **FINRA Gateway free bond lookup** — its Fixed Income Data User Agreement **prohibits**
  "any robot, spider, other automatic or manual process to monitor or copy the Data", bans
  bulk download beyond personal non-commercial use, and forbids redistribution (plus
  Refinitiv/ICE/Moody's restrictions). **Do not automate it.**
- **Conclusion:** no free, licence-clean source of intraday per-trade corporate bond prints
  exists. Per-bond *valuations* come from **SEC N-PORT** (adapter built): quarterly, public
  domain, CUSIP/ISIN/LEI + par balance + USD fair value + maturity/coupon + ASC 820
  `fairValLevel`. Implied price = valUSD/balance*100, computed in analytics (I3), not ingest.
  Municipal per-trade prints remain available free from MSRB EMMA (not yet built).

## Known deviations from the spec

*Anything built differently from what the spec says, with the reason and the authorising decision record. Empty is the goal.*

- Single-machine substitutions per `CLAUDE.md` §2 and §3 — authorised, interfaces preserved

---

## Session log

*Newest first. Two or three lines each: what was done, what broke, what is next.*

### 2026-07-27 — WP7: GLEIF relationship-record entity graph
Continuation of the 2026-07-26 session (this entry also backfills that session's last four
commits, `1bfbbe2`..`3bbda6d`, which were never logged here: WP6 completed — N-PORT per-bond
valuations, TRACE `treasuryDailyAggregates`; WP7 core — FIGI-tier identifier resolution in
`core/master.py`; and the continuous-verification suite — nightly deep workflow, fixture-drift
tests, `pip-audit`, mutation testing — all six items now landed, not just designed).

This session: ran `make test` for the first time end-to-end (33 min wall-clock, 2s-scale CPU —
the sandbox I/O bottleneck noted previously is confirmed, not transient). 193 passed at 84.75%
coverage; recalibrated `--cov-fail-under` from the placeholder 80 to 84 per Jack's instruction
to calibrate to the real measured figure.

Built the entity-graph half of WP7 (spec §9.5's primary source): downloaded today's live GLEIF
Level 2 Relationship Record concatenated file directly (660,674 records) to get the real RR-CDF
2.1 schema rather than guess at it — confirmed every `StartNode`/`EndNode` is LEI-typed with no
exceptions, and found the six relationship-type values as GLEIF actually spells them (including
`IS_FUND-MANAGED_BY`, which the prose documentation renders differently). Trimmed 8 real
records (all 6 types, ACTIVE/INACTIVE/NULL status) into `tests/fixtures/gleif/rr_sample.xml`.
Built `GleifRelationshipAdapter` (discovers the current publish id via the metadata endpoint,
since the download URL's id increments daily — CLAUDE.md's no-guessed-endpoints rule) and
`core/entity_graph.py` (direct/ultimate parent, reverse `children()`, point-in-time resolution,
conflict reporting — mirrors `core/master.py`'s identifier-resolution pattern deliberately).
18 new tests, all green; added the `gleif-rr` fixture-drift check in the same commit as the
adapter, per the standing continuous-verification requirement, and ran it live (not just added)
to confirm it actually catches a real schema.

Not done: EDGAR Exhibit 21 and OpenCorporates, spec §9.5's other two entity-graph sources.
Security-master *population* for the full configured universe has not been attempted — there is
no `config/universe.yaml` or resumable ingest runner yet; this remains the real gap against the
Phase 1 checklist item and needs a scoping decision (see Current position) before starting.
Also flagged: the "completion-percentage model" a prior session said it recorded to memory
is not in the persistent memory store (empty). Recompute the ~13.13% figure once that model is
confirmed/re-saved — do not treat it as current until then.

### 2026-07-26 — suite green; WP5/WP6 landed; six harness catches
Continuation of the 2026-07-25 session. Full gate went green (167 tests) after the harness
caught six real defects, none reaching a commit: (1) QuantLib 1.43 removed the float-price
`Bond::yield` overload → `ql.BondPrice`; (2) payment dates silently rolled by QL's default
Following convention on UNADJUSTED bonds → spec convention passed through; (3) content-
addressed ids were timezone-representation-sensitive → all stored datetimes canonicalised
to UTC (regression-pinned); (4) DuckDB TIMESTAMPTZ needs `pytz` → dependency added; (5)
ingest log lacked source URI → replay wasn't byte-identical → column added; (6) scipy quad
stepped over a 2e-6-wide spike in the Hagan–West shape function (Hypothesis-found) → the
*checker* was wrong, closed form exact; breakpoints now passed to quad. WP6 adapters
(EDGAR companyfacts+submissions, OpenFIGI envelope-payload, GLEIF) all fixture-tested with
live-recorded payloads; FIGIs/LEIs cross-validated against our own checksum implementations.
CI: setup-python ordering fixed; trimmed to ubuntu-only (cost directive). Environment note:
this sandbox does not process .pth files — root conftest.py pins sys.path (keep it).
Decisions added: release = full spec at launch; completion-percentage model in memory prefs.
Jack added a Phase 1 criterion (pane-form validation in CI) citing spec §6.4; recorded
against §6.1 pending his spec revision — his edit had not reached disk (verified via git).

### 2026-07-25 — Phase 0 complete; WP0–WP4 built
Read spec, CLAUDE.md, PROGRESS.md in full. Produced the Phase 0 plan (invariant mechanisms +
kill-tests, screen contract, WP0–WP15 breakdown, open questions); Jack answered the three
blocking questions (CI = GitHub Actions; universe = all EDGAR filers; OAS = lattice with user
vol) and approved. Wrote ADR-0001..0005 (0002 amended: in-repo interpolator protocol behind
the generic bootstrap; QuantLib as cross-check).

Built and tested:
- **WP0** — uv env on Apple Silicon (QuantLib 1.43 arm64), pre-commit, GitHub Actions
  workflow, private remote `dgfc92tdp9-crypto/treble-tracker`, first pushes green locally.
- **WP1** `core/` — FIGI (check digit) / LEI (mod-97) / TUID, yellow keys, security-reference
  parsing for every §5.1 form; `Provenance` content-addressed DAG + generic `trace` (SPTR);
  bitemporal immutable `Fact` (no stored `knowledge_to`, ADR-0001).
- **WP2** `store/` — content-addressed `PayloadStore` (put/get/exists, corruption detection),
  append-only DuckDB `IngestLog`, `Store`/`HistoryStore` protocols with **no mutation members**
  and required keyword `as_of`; `DuckStore` with latest-knowledge-wins reads; I1 dangling-
  provenance rejection; I2 Hypothesis property (no `knowledge_from > as_of` ever); I5
  deterministic-replay test at the storage layer.
- **WP3** `render/contract/` — screen schema (semantic attrs, closed predicate set, panes,
  tabs), generic resolver `resolve(def, ctx, as_of, tapi) -> CellBuffer`, canonical layout-tree
  + text-snapshot projections, conformance harness with 3 synthetic cases and goldens;
  renderer registry seeded with the reference renderer (TUI/web plug into the same suite).
- **WP4 (part)** `analytics/` — `_ql.py` (locked evaluation-date context manager; cached
  calendars/day counters; the only Settings-touching module), `@model` registry + envelope
  (I3; auto-captures `content_hash`-bearing inputs), `CurveConfig` content-addressed (I4,
  pinned golden hash), Hagan–West monotone convex in-repo (quadrature-validated closed-form
  integrals), interpolator set (linear zero, log-linear DF, natural/monotonic cubic, monotone
  convex), global-solve bootstrap enforcing 1e-10 repricing at construction, across all
  methods, on real QuantLib calendars/day counts.

**Environment note:** the Claude Code sandbox slows uv/mypy/pytest I/O badly (mypy ~9 min
wall for 2s CPU); mypy cache is pointed at the session scratchpad as a workaround. CI on
GitHub runners is unaffected.

Still open in WP4 before WP5: QuantLib cross-check golden for the log-linear bootstrap;
Hagan–West paper worked-example golden (needs the paper table — do not fabricate values).
Next: finish WP4 validation goldens, commit per criterion, then WP5 bonds/YAS.

### (not yet started)
Repository scaffolded with layout, tooling, invariant enforcement config, and the four orientation
documents. Awaiting first working session.
