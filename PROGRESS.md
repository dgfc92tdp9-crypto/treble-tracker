# PROGRESS

Live build state. **Update at the end of every session.** Write it for a reader who remembers nothing — after a context reset, that reader is you.

Do not restate the spec or `CLAUDE.md` here. This file holds only: where we are, what is blocked, and what was decided.

---

## Current position

**Phase:** 1 — research workstation
**Status:** WP0–WP5 complete; WP6 ~90% (EDGAR/FRED/Treasury/OpenFIGI/GLEIF done with real
recorded fixtures; TRACE-file pending endpoint investigation). Full local gate green at
`239c48d`: 167 tests, mypy --strict, import contracts, ruff incl. security (S) rules.
Completion vs the fixed model (see memory/working prefs): ~13.13%.
**Next action:** confirm ubuntu CI green on `239c48d`; TRACE-file adapter (investigate the
real public download path first — no guessed endpoints); then WP7 security master; then the
vertical slice (WP8 TAPI → WP10 cmd → WP11 DES/YAS → WP12 TUI → WP14 local-only) is
prioritised ahead of full breadth so a launchable TUI exists at the earliest date.
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
- [x] **Coverage floor in CI** — `--cov-fail-under=80` in pytest addopts; untested new code
      cannot land (would have caught the renamed-but-unexercised `TraceCredentialsMissing`
      call sites)
- [x] **Mutation testing** — `make mutate` (mutmut over `analytics/` and `core/`), on demand
      because it is slow; proves the suite detects damage rather than merely passing
- [x] **`pip-audit` in CI** — dependency vulnerabilities disclosed after shipping

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
