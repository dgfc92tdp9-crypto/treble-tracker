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

**Phase:** 2 — real-time, portfolio, risk (Phase 1 complete and green on a clean checkout)
**Completion: 54.91%**

### Known quality gap: `tapi/local.py` at 60% (2026-08-08)

The screen-binding layer is the least-covered module in the repository —
190 statements missed, against a repository floor of 84% and a whole-suite
figure of 89%. The module-coverage gate cannot see it, because that gate
asks whether a module has *any* coverage, not whether it has enough.

Attributed by method, so the next person does not have to guess where to
start:

| method | missed | added |
|---|---|---|
| `_swpm` | 22 | earlier |
| `_swpm_basis` | 18 | earlier |
| `_port` | 15 | earlier |
| `_tval_residual` | 14 | 2026-08-08 |
| `_tval` | 13 | earlier |
| `_swpm_ois` | 13 | earlier |
| `_allq` | 12 | earlier |
| `_vcub` | 10 | earlier |

Six binding methods were added on 2026-08-08 and account for 34 of the
190. The largest of those, `_tval_residual`, is tested only on its
empty-store path: the populated path was verified by running it against
the live store (106 bonds, -10.5% skill) and never pinned by a test, so a
regression in it would be silent.

Every one of these methods turns stored facts into screen rows, which is
where a wrong number reaches a person. The fixtures are the reason the
coverage is low — each needs a store populated with curves or holdings.

**`tests/storebuilder.py` is that cost paid once** (2026-08-08).
`StoreBuilder().with_curves(usd=True).with_bonds()` gives a store an
issuer curve can be fitted through, and `_tval_residual`'s populated path
is now pinned rather than resting on one manual run.

The builder was the point, and the second and third uses show why:

| after | coverage | missed |
|---|---|---|
| baseline | 60% | 190 |
| `_tval_residual` pinned | 62% | 179 |
| four SWPM panels | 73% | 129 |
| three TVAL panels | 76% | 112 |

Seven panels, no new fixture code — all of them needed only curves or
bonds, which the builder already had. That they went untested for so long
was never about difficulty; it was that each would have paid the fixture
cost alone.

What remains needs the builder extended: `_port` wants factor returns,
`_allq` contributions, `_vcub` swaption prints. `with_factors`,
`with_contributions` and `with_swaptions` are perhaps twenty lines each.

### Phase 2 gate audit (2026-08-07)

Every criterion in CLAUDE.md §8 Phase 2 audited against test evidence, on a
green `make gate` (1,704 tests, 89% coverage, no module unmeasured).

| Criterion | Tests | Status |
|---|---|---|
| Ticker plant, conflated + unconflated TPIPE | 131 | met |
| `ALLQ` correct-when-empty; contribution API | 28 | met |
| `PORT` with TFM3 v1; validation tests passing | 66 | met |
| `TVAL` v1 with score and transparency drill-down | 60 | met |
| `CDSW` against ISDA published test cases | 65 | met |
| `SWPM` multi-curve CSA-aware discounting | 242 | met |
| Canvas with FDC3 context propagation | 55 | met |
| gRPC + Arrow Flight transports | 54 | met |

**All eight gate criteria are met.** The ledger fractions below 1.00 track a
wider scope than the gate: the spec's full §12.1 product list, §15.4/§15.5,
layout gestures, `ems`. That gap is deliberate and worth stating plainly —
the checklist is the phase gate, and the ledger is what the spec asks for in
total. Reporting the phase as incomplete because the ledger is would
misstate the gate; reporting it as finished because the gate is would
misstate the spec.

Remaining ledger items, none of which is a gate criterion:

- **P2_4 §15.5** multi-time bid/mid/ask snapshots — data-blocked, no free
  intraday quote source.
- **P2_7** drag/resize gestures in the Tauri shell (renderer wiring onto
  `render/layout.py`), and FDC3 federation, which needs a real desktop agent.
- **P2_8 `ems`** — Phase 3 by the spec (§23.3 puts FIX connectivity there).
 — computed by `python scripts/completion.py`, never written by hand.

> **The figure is generated, not stated.** `config/completion.yaml` is the ledger: fixed phase
> weights (P1 30 / P2 25 / P3 15 / P4 20 / P5 10) and a fraction per work package. The script
> computes; `tests/test_completion.py` fails the gate if this file disagrees with it, if the
> weights do not sum to 100, if Phase 1 does not have 16 packages, or if any partial lacks a
> `basis` saying what was counted.
>
> **Why it exists.** The number was written by hand and one update got it wrong twice over: the
> phase weight was reverse-engineered from an earlier reported figure (33.33%) instead of read
> from the model (30%), and WP11/WP12 were credited by impression when one screen of eleven
> existed and one renderer of two. Neither was an arithmetic error — both were preferring a
> recalled value to the recorded one. The first run of the new test caught this section still
> claiming 20.72% against a computed 20.42%, which is the mechanism doing its job on its
> author.
>
> Reported figures over time: 28.13% (wrong — bad weight, over-credited partials), 23.02%
> (wrong weight only), 20.72% (correct weight, partials still by impression), 20.42%
> (computed; WP8 0.8 and WP11 0.09 with a stated basis), 21.09% (WP11 0.45 — five of
> eleven screens), 21.28% (WP11 0.55 — ICVS), 21.64% (WP11 0.64 and WP8 0.9 — YAS with analytics through TAPI), 21.98% (WP11 0.82 — GP and HP on the Index namespace), 22.72% (WP9 0.4 — the
> TQL grammar; WP6 1.0 with bulk XBRL), 23.47% (WP9 0.8 — planner and executor), 23.76% (WP9 0.95 — overrides reach the models), 24.19% (WP9 and WP11 complete —
> SRCH and EQS are TQL-backed screens; all eleven screens exist), 26.06% (WP14 — `treble init`), **27.94%** (WP13 — the spreadsheet add-in).

**Status:** **Phase 1 is complete** — 16/16 work packages, 12/12 gate criteria, green in CI on a
clean checkout. Phase 2 is in progress; see the table below for per-criterion state.

Phase 1 left a workstation that is real rather than demonstrative: **8 namespaces, ~10,000
subjects, ~8.1M facts**, a Tauri desktop app that opens from the Dock, thirteen screens rendering
identically on two surfaces from one definition, and bond analytics that reproduce the US
Treasury's own auction yields to **0.07bp** worst case across 46 auctions.

**Next action: gRPC + Arrow Flight transports (spec §8.3).** Chosen because it is the only thing
keeping `ALLQ` at 0.95 — the contribution service is in-process, so a remote participant cannot
contribute — and it has no data dependency. Arrow Flight fits a store that is already
DuckDB/Parquet/Arrow: TQL result sets and bulk universe pulls are the natural payloads.

Then, in rough order of what is unblocked:

- **`TVAL` Prong 2** — issuer curves and the screen both ship. What remains is the
  rating/seniority data the similarity metric declares missing, plus §15.4/§15.5.
- **Canvas + FDC3** — UI work, no data dependency.
- **`PORT` / TFM3** — model and screen both ship. What remains is per-name equity coverage
    (still absent), and the factor breadth §16.2 describes.
- **`CDSW` to 1.0** — needs ISDA's published test cases.
- **Ticker plant to 1.0** — more venues (only Coinbase crypto is reachable free), security master enrichment, Redpanda and NATS transports.

Deferred deliberately, not forgotten: EDGAR Exhibit 21 / OpenCorporates (spec §9.5 breadth).

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

Criteria copied from `CLAUDE.md` §8. **All twelve verified green in CI on a clean checkout**
(WP15 gate audit, commit `13a87b4`). One gap was found and fixed during the audit: I7 had no
test protecting its own `.importlinter` config, so deleting the contracts left `lint-imports`
exiting 0.

- [x] All seven invariants have enforcement mechanisms, each with a test that fails if the mechanism is removed
- [x] Screen definition schema, resolver contract, and conformance suite exist; both renderers pass it
- [x] Command grammar parses every example in spec §5.1 plus a fuzz corpus; yellow-key namespace resolution correct
- [x] Ingest adapters: EDGAR, FRED, Treasury, TRACE-file, OpenFIGI, GLEIF — each with offline fixture tests
- [x] Security master and entity graph populated for the configured universe subsets
- [x] Screens working in both clients: `DES` `FA` `GP` `HP` `YAS` `ICVS` `SRCH` `EQS` `FLDS` `SPTR` `MDL`
- [x] Curve bootstrapping reprices inputs to 1e-10 across all supported interpolation methods
- [x] `YAS` golden-value tests passing against published references
- [x] TAPI with Python client; `TDP`/`TDH`/`TDS` spreadsheet functions via xlwings
- [x] TQL parses and executes the spec §4.2 example
- [x] Local-only mode: one command from clean checkout to working workstation
- [x] Pane form selection enforced at load time; CI validates every `.screen.yaml` against
      the screen-definition contract (spec §6.1 — requested by Jack citing §6.4; §6.4 is
      currently the charting engine, reconcile if the spec is revised)
- [x] `PROGRESS.md` current

---

## Phase 2 — real-time, portfolio, risk

Criteria in `CLAUDE.md` §8. The ledger (`config/completion.yaml`) tracks one entry per criterion,
because here the criteria *are* the deliverables — unlike Phase 1 there is no separate work-package
plan, and inventing one would put a second set of numbers beside the gate's own.

**Verified state, 2026-08-10** — measured on the working tree at `34b8fdc`:

| | |
|---|---|
| `make gate` | green |
| tests | 1,985 collected, 11 deselected |
| screens registered | 16 — ALLQ DES EQS FA FLDS GP HP ICVS MDL PORT SPTR SRCH SWPM TVAL VCUB YAS |
| tabs across them | 28, each with its own conformance case |
| conformance cases | 33 |
| analytics carrying an I3 envelope | 57 |
| ingest adapters | 19 |

Against the 2026-08-06 audit: tests 1,472 → 1,985, I3 analytics 45 → 57, adapters
16 → 19. The per-tab conformance requirement is new (2026-08-10) and was added
after a tab shipped with nothing checking it.

Five screens were added in Phase 2 and each one closed a gap between an analytic and a user:
`SWPM` gained OIS and tenor-basis tabs, `PORT` reached the factor model, `TVAL` the issuer
curves, `VCUB` the volatility surface. That gap — working analytics nothing can display — was
the single most common defect found in this phase, and it is worth checking for by default.

**The per-criterion numbers live in `config/completion.yaml` and are deliberately
not repeated here.** A table of the same eight figures in a second file is a
second source of truth, and this one had already drifted: it still showed the
ticker plant at 0.78, `PORT` at 0.60 and `TVAL` at 0.75 while the ledger read
1.00, 1.00 and 0.98. The ledger's own comment warns against exactly this —
"inventing one would put a second set of numbers beside the gate's own" — and
then a prose table did it anyway.

Phase 2 as at 2026-08-10: **P2_1 through P2_8 = 1.0, 1.0, 1.0, 0.98, 1.0, 1.0,
1.0, 0.99.** The residual 0.03 is not unbuilt work:

- **P2_4 (0.98)** — rating and seniority. Seniority is measured absent from
  N-PORT (0 of 460 debt titles carry a token; the schema has no such field).
  Ratings are legally public under Rule 17g-7(b) and blocked by per-vendor
  terms at every NRSRO whose terms could be read.
- **P2_8 (0.99)** — `ems` needs an execution venue, which the spec puts in
  Phase 3 (§23.3).

**The swap curves exist, and the whole chain is verified on them.** The DTCC SDR adapter
(2026-08-02) ingests CFTC Part 43 public price dissemination — ~20,000 interest-rate prints a
day — and builds **four curves**, 15 trading days each in the store:

| Curve | Role | Latest 10Y |
|---|---|---|
| `USD-SOFR-OIS` | discounting | 4.306% |
| `EUR-ESTR-OIS` | discounting | 2.970% |
| `EUR-EURIBOR-6M` | forecasting, 6M index | 3.218% |
| `EUR-EURIBOR-3M` | forecasting, 3M index | 3.108% |

This is the source spec §11.1 names, and the only free one: FRED discontinued its swap series in
2016 and the Treasury CMT curve is a government curve, not a swap curve.

**The euro pair is what makes `SWPM` work end to end.** An overnight index compounds daily, so
an OIS curve discounts but cannot project a discrete index — the pricer refuses it as a forecast
curve. EURIBOR is a term index and can. On real prints, ESTR discounting against EURIBOR-6M
forecasting gives a **0.0000bp** cross-check (a trade matching a curve input reprices to its
quote through the independent cash-flow pricer) and breaks the single-curve telescoping identity
by **EUR 2,110,467 on EUR 100m**.

**The index tenor is not in the payload's index name.** `EUR-EURIBOR` is the underlier for both
3M and 6M swaps; only the floating leg's reset frequency separates them. Merging them would blend
two curves that a tenor basis separates by about 11bp, and the merged curve would look entirely
ordinary.

> **DTCC terms of use are UNVERIFIED — read this before extending the adapter.**
> DTCC's terms live at `https://www.dtcc.com/legal.php`, which is served behind Cloudflare bot
> protection and returns HTTP 403 to any non-browser client. They could not be read, and
> circumventing the block was refused (same line as the Stooq refusal — see Data access
> findings). DTCC separately sells *OTC Direct Connect*, a paid systematic-delivery product for
> this same dashboard data. **Jack was shown both facts on 2026-08-01 and chose "build it
> anyway"**, accepting the ToS risk explicitly. That is a recorded decision, not an oversight.
> The source is marked `redistribution_restricted` and throttled to one request per five
> seconds. If the terms are later read and prohibit automated access,
> `treble/ingest/dtcc.py` is the thing to delete.

> **Phase 2's remaining criteria are mostly blocked on market data, not effort.** Audited
> 2026-08-03 against the live store. The recurring shape: free public filings give *positions and
> fundamentals*; Phase 2's risk and real-time criteria need *market price series*, which is
> exactly the data that is not free.
>
> | criterion | blocker |
> |---|---|
> | `PORT` / TFM3 | **partial** — factor returns come from Ken French (below); per-name equity history still absent |
> | `TVAL` Prong 2 | **partial, and narrower than recorded** — the issuer-curve half needs only issuer, maturity and price, all present: 35 curves fit on live marks. The similarity half still lacks rating and seniority and reports them as missing rather than dropping them. `nport:issuerCat` is an N-PORT category, not a sector classification |
> | Ticker plant | **partial, and narrower than recorded** — 'no free trade or tick data' is true of equities and every consolidated tape, and false in general: Coinbase publishes its own matches over an unauthenticated WebSocket, live and correctly sequenced. Crypto only. (`TRADE_COUNT` in the store remains the DTCC adapter's metadata about how many prints backed a curve node, not prints) |
> | `SWPM` breadth | **hard** — swaptions/CMS need a `VCUB` vol surface; no swaption vol data |
> | `CDSW` | external — ISDA's published test cases |
> | **Canvas** | **none** — renderer wiring only |
>
> A method note, because the first attempt at this audit was wrong and would have concluded the
> opposite: substring-matching field names hit XBRL *concept* names, so "sector" matched
> `...PerBasicShare` and "rating" matched `OperatingIncomeLoss`. Every hit was a false positive.
> The table above is from fields actually present on `cusip:`/`isin:` subjects.

> **`PORT`/TFM3 was recorded as hard-blocked, and the block was drawn too wide.** The
> measurement on 2026-08-03 was correct: the store had *no per-name equity price history*.
> `PX_LAST` covered 36 FRED series (index levels, not constituents), 3 crypto and 3 FX pairs,
> and N-PORT implied marks gave 1,861 names across **three** report dates — two return
> observations per name, which cannot estimate a covariance at any number of factors.
>
> The conclusion drawn from it did not follow. That is a block on *per-name* history, and a
> factor covariance does not need per-name history — Fama and French publish the factor
> returns themselves. Since 2026-08-04 the store ingests the Kenneth R. French Data Library:
> FF5 plus momentum daily since 1963-07-01, and the 49 industry portfolios, whose own return
> series make them usable as assets with estimable exposures. §16.3 now ships and is validated
> out-of-sample (see the risk model note below).
>
> **What is still blocked, precisely:** a portfolio of *individual equities*. Estimating a
> stock's factor exposures needs that stock's return history, which the store still lacks, so
> the model covers portfolios expressed over the published portfolios and not the equity
> universe.
>
> **Re-tested 2026-08-06, and this one holds.** Four other blocks recorded on this project
> turned out narrower than their evidence, so this was measured again rather than carried
> forward. N-PORT is the only free per-name equity mark: holdings are reported once per
> filing at `repPdDate`, so marks are **quarterly** — the `mon1/mon2/mon3` tags are
> fund-level returns and flows, not per-holding. Vanguard Index Funds' earliest N-PORT filing
> is **2019-05-30**, capping any per-name history at roughly **28 quarterly observations**.
> `MIN_OBSERVATIONS` is 60, and that floor is right rather than cautious: 28 quarterly points
> cannot support a covariance over six factors. Only 7 N-PORT filings are currently ingested,
> so today's panel is thin from under-fetching — but fetching all of them still lands under
> the floor. Unblocking needs a daily or monthly per-name price source, and every free
> candidate is registration-gated. **That is an account decision, not a modelling one.**
>
> The general lesson is recorded as failure class E: an explanation asserted rather than
> tested. "No per-name prices" was measured; "therefore no factor model" was not.

**The `SWPM` screen ships** (2026-08-02): three tabs — valuation, curve environment, cash flow
schedule — rendering on both surfaces from one definition, with a conformance case per tab. It
shows a spot-starting 10-year par swap against the live ESTR/EURIBOR-6M pair: PV, par rate,
annuity, DV01 of EUR 85,861, thirty cash flows, and the basis per tenor. The trade is a
**template, not a position** — this system books none — and the screen says so on its face.

What remains on this criterion is spec §12.1's wider product set (swaptions, caps/floors, CMS,
cross-currency with MtM resets, inflation, asset swaps, cancellable/extendible, total return),
not the multi-curve CSA-aware discounting the criterion names.

Keeping the curve current means extending `dtcc_report_dates` in `config/universe.yaml`; dates
are explicit rather than a rolling window so each step has a predictable URI and population stays
resumable. A `discover` mode is the follow-up.

## Phases 3–5

Not started. Criteria in `CLAUDE.md` §8. Do not begin a phase until the previous one is fully green.

---

## Open questions

*Blocking questions first. Remove once answered, and record the answer as a decision if it was architectural.*

Non-blocking, proceeding on stated defaults (flag if wrong):

- ~~Spreadsheet add-in host~~ — **settled 2026-07-31**: Microsoft Excel is installed on the dev Mac, so WP13 targets xlwings as planned. LibreOffice Calc is not installed and was not needed.
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
- [0006](docs/decisions/0006-curve-config-carries-index-and-leg-conventions.md) — `CurveConfig` carries the index tenor and the swap legs' conventions; the I4 pinned hash is re-pinned once

---

## Verification practice (standing, learned the hard way 2026-07-27)

**Run `make gate` before every commit. Do not commit with `--no-verify`.**

Three times on 2026-07-27 a check was piped into `tail`, its exit code was
masked by the pipe, the output was read as if it were a pass, and a failing
state was committed. Twice more, `--no-verify` was used to skip the
pre-commit hook — originally justified when iCloud made the suite
unrunnable, and left in place after that reason disappeared.

The compensation is mechanical, not aspirational:

- `scripts/gate.sh` (`make gate`) runs every check under `set -euo
  pipefail`, so a failing stage stops the script whether or not anything is
  piped. It prints `GATE GREEN` only when everything passed.
- The pre-commit hook now runs the test suite as well as lint and types, so
  the local gate matches CI. With the repo out of iCloud the whole thing
  takes ~15s; there is no longer any excuse to bypass it.

**Jack's standing instruction (2026-07-27):** "Always try to learn and
compensate for every mistake found/made. Time doesn't matter, only that it
is the highest level project produced." Every defect found — in the code or
in the process — gets a mechanism that prevents its recurrence, not a note
to be more careful.

## Phase 1 gate audit (WP15, 2026-07-31)

Each CLAUDE.md §8 Phase 1 criterion, checked rather than asserted. Verdicts
are from running the thing, not from reading the code that implements it.

| Criterion | Verdict |
|---|---|
| Seven invariants, each with a kill-test | **met, after a fix** — I7 had none; see below |
| Screen schema, resolver, conformance suite; both renderers pass | **met** — 3 renderers registered, 14 cases |
| Command grammar parses §5.1 plus a fuzz corpus | **met** |
| EDGAR, FRED, Treasury, TRACE-file, OpenFIGI, GLEIF adapters with offline fixtures | **met** — plus bulk XBRL and N-PORT |
| Security master and entity graph populated | **met** — 5,907 filers, 2.02M facts |
| Eleven screens working in both clients | **met** — each with a conformance case on the shipped definition |
| Curve repricing inputs to 1e-10 | **met** — asserted as a property on every curve |
| `YAS` goldens against published references | **met** — and independently against 46 Treasury auctions to 0.07 bp |
| TAPI with Python client; TDP/TDH/TDS via xlwings | **met** |
| TQL parses and executes the §4.2 example | **met** |
| One command from clean checkout to working workstation | **met** — `treble init`, verified with sockets blocked |
| PROGRESS.md current | **met** |

**Verified on a clean checkout**, not only in the working directory: fresh clone,
`uv venv`, `uv pip install -e ".[dev]"`, `treble init`, then the full gate — green at
88.38%. CI green on the audited commit `13a87b4`.

**Phase 1 is complete.** All sixteen work packages, all twelve gate criteria.

**What the audit found.** Criterion 1 says every invariant needs "a test that
fails if the mechanism is removed". I7's mechanism is `lint-imports` against
`.importlinter`, and the gate runs it — but nothing protected the config
itself. Deleting a module from the forbidden list leaves `lint-imports`
passing with less to check, and every other test stays green. Demonstrated:
with `treble.store` removed from the forbidden list, `lint-imports` exits 0.

`tests/test_i7_contracts.py` closes it by asserting both contracts still
exist, name the right modules, and declare the seven layers in order — and
that `gate.sh` still runs the linter, because a correct config nothing
executes enforces nothing. Verified to fail against the weakened config and
pass against the real one.

This is the second time an enforcement mechanism turned out to be
unenforced (the first was the universe loader silently dropping a config
key). Both share a shape worth naming: **the mechanism worked, and nothing
checked that the mechanism was still switched on.**

## Failure modes and what catches them (standing, Jack 2026-07-27)

> "I want you to update Progress and your method of approach so that simple mistakes like
> these don't come up as often if not at all. Also adjust for code errors that keep coming up
> and the general way they come up so that you learn from your mistakes."

Every mistake made on this project so far falls into six classes. They are recorded by
*class* rather than as a list of incidents, because the same shape keeps recurring in new
material — a note about one incident would not have prevented the next one. Each class names
the mechanism that now catches it, and says honestly whether that mechanism is enforced by
the gate or is a rule that depends on discipline.

### A. Success read from output that could not report failure — 8 occurrences

`ruff check . | tail`, `pytest | tail`, and most recently `npx tauri build > log 2>&1; echo
"EXIT=$?"; tail -25 log`. In every case the exit status came from the last command in the
chain, the output looked fine, and a failing state was accepted. The Tauri build reported
exit 0 while the compile had failed on a missing icon.

- **Enforced:** `scripts/gate.sh` runs every check under `set -euo pipefail`; the pre-commit
  hook runs the same suite so a bypass requires `--no-verify`, which is forbidden.
- **Enforced:** multi-step builds live in `scripts/*.sh` with `set -euo pipefail` and an
  explicit post-condition — `install_desktop.sh` fails if a "successful" build produced no
  bundle, because a stale bundle copied after a failed build is indistinguishable from success.
- **Enforced:** `tests/test_makefile.py` — every Makefile target must be declared `.PHONY`.
  `make check` depends on `proto`, `proto` was not phony, and a directory called `proto/`
  exists, so make treated the target as an up-to-date file and skipped the recipe on every
  run since gRPC landed. `make proto` printed "`proto' is up to date." and generated nothing.
  The stubs are gitignored, so **CI was red on a clean checkout the whole time** — verified
  2026-08-06 by cloning to a fresh directory: at the parent commit `make proto` refused to run
  and `tests/tapi/test_grpc.py` errored at collection; after the fix `make check` exits 0 with
  1,317 passing. This is also a class D failure, and is counted in both.
- **Rule:** never end a command with a pager or `tail` and read its status. Redirect to a
  file, capture `$?` into a variable or file *before* anything else runs, then read the log.
- **Rule:** a build step that is *supposed* to run is not known to run. Check that it produced
  something, on a checkout that has nothing lying around from a previous run.

### B. A recalled value preferred over the recorded one — 4 occurrences

The completion percentage (phase weight reverse-engineered from an earlier figure instead of
read from the model). The web renderer's JSON (`json.dumps` parameters reproduced by hand,
`ensure_ascii` wrong, goldens diverged). The `.env` loader (the variable had been exported by
hand in the shell, which masked for days that the CLI never read `.env` at all). The iCloud
diagnosis (a theory about the sandbox preferred over measuring, until Jack's report that
Terminal was also slow disproved it).

- **Enforced:** `config/completion.yaml` + `scripts/completion.py` + `tests/test_completion.py`
  — the number is computed, and the gate fails if PROGRESS.md disagrees.
- **Enforced:** `canonical_json()` is the single serialisation point for layout goldens; there
  are no parameters left to reproduce by hand.
- **Rule:** when two places must agree, make one derive from the other. Where that is
  impossible, add a test that compares them. Never reconstruct a constant by dividing two
  numbers you already suspect.

### C. A check that could not have failed — 2 occurrences

A verification server was pointed at `~/.treble` while the real store is `data/`; it created
an empty database, served a screen of honest-looking dashes at 200 OK, and that was read as a
passing check. A test asserted "contains a digit" as a proxy for "is a figure" and was
satisfied by the static menu label `1) FA Financial Analysis`.

- **Enforced:** `DuckStore.fact_count()`, checked at client startup, so an empty store
  announces itself instead of rendering plausible emptiness.
- **Enforced:** `DEFAULT_DATA_DIR` is absolute, so which store opens no longer depends on the
  working directory.
- **Rule:** a check that has never been observed to fail is not evidence. Before trusting a new
  test or a manual verification, confirm it fails against the broken state. Assert the precise
  property (no cell carries provenance), never a proxy for it (no text contains a digit).

### D. An environment assumption never stated — 5 occurrences

A relative `Path("data")` for the store. A network fetch on every launch, making the desktop
app unopenable offline. An icon set with the `.icns` but no `.png`. An HTTP server placed in
`treble/tapi/` when it imports `treble.render`.

- **Enforced:** `lint-imports` caught the layering, as designed (I7).
- **Enforced:** `tests/cmd/test_data_dir.py` pins cwd-independence;
  `tests/ingest/test_company_index_cache.py` pins the offline path.
- **Rule:** paths anchored absolutely, never relative to the caller's cwd; assume no network at
  startup; a build that has only ever run on this machine has not been shown to be portable.
- **Rule (added 2026-08-06):** *a green local run is not a green CI run.* The `proto` target
  above passed locally for weeks because the generated directory happened to exist from a
  manual run, and failed on every clean checkout. When a mechanism's effect is a generated or
  downloaded artefact, verify it on a fresh clone, not on the working tree that already has
  one.

### C(ii). A guard whose condition never matched — 1 occurrence

`treble/ingest/nport.py` keyed every holding by CUSIP or ISIN, and skipped
those with neither: *"Unidentifiable holdings are skipped, never guessed at."*
N-PORT filers write `cusip=N/A` for holdings that have no CUSIP — chiefly OTC
derivatives — and `holding_subject` accepted that literal string as an
identifier. The skip had therefore never happened. Every unidentified holding
across every filing keyed to the **same** subject: `cusip:N/A` carried 2,110
facts across 26 fields on the live store, and `cusip:000000000` another 932,
each position silently overwriting the last one's fields.

This is class C — a check that could not fail — with the twist that the check
*existed and read correctly*. Nothing about the code said the guard was inert;
the placeholder simply was not on the list of things that count as absent.

- **Enforced:** `_NULL_IDENTIFIERS` and `_identifier()`, with tests
  parametrised over `N/A`, `n/a`, ` N/A `, `000000000`, `0`, `NONE` and the
  empty string, plus an end-to-end assertion that no parsed holding lands on
  a subject whose identifier is one of them.
- **Rule:** a sentinel is not a value. When a source writes "there isn't one"
  as a string, the parser must be told which strings those are — and the test
  must use the source's own spelling, not a guessed one.

### E. An explanation recorded as fact, never tested — 1 occurrence

`tests/analytics/credit/test_isda_grid.py` asserted that its residual against ISDA's published
grid "is discretisation", and named the remedy: "real IMM payment schedules and a finer
protection integral". Both halves were false. Sub-dividing the protection integral 2, 4, 8 and
16 times moved the worst AUD case from 6.5125bp to 6.5127bp — no effect at any resolution.
Replacing the uniform schedule with the real IMM roll schedule made every grid roughly twenty
times worse. `config/completion.yaml` had copied the same explanation, so the wrong cause was
recorded in two places and gated in neither.

What makes this its own class: every *assertion* in that test passed, before and after. The
error was entirely in prose, which no mechanism reads. A wrong explanation is worse than none,
because it is load-bearing for every later decision that trusts it, and a green suite actively
vouches for it — the next person does not re-derive an attribution that appears settled.

- **Enforced:** the test now records both refutations with their measurements, so neither is
  re-attempted, and states the surviving candidate as an open lead rather than a conclusion.
- **Enforced:** `test_the_worst_case_is_the_known_shape` fences the widened tolerance, so the
  room left for an unexplained residual cannot be spent by an unrelated regression.
- **Rule (added 2026-08-06):** a *measurement* is a claim too. An audit table of
  "fields published versus fields read" was committed with the read count measuring facts
  *written* — so `dtcc-sdr` appeared to ignore 106 columns it actually consults. Recounting
  by string literals broke the other way, scoring adapters that iterate keys generically as
  reading almost nothing. Neither number was wrong arithmetically; both were presented as
  meaning something they did not measure. State what a count counts, and say when it cannot
  distinguish the cases you are drawing a conclusion between.
- **Rule:** an attribution written in a docstring, a commit message or the ledger is a claim,
  and claims get tested or get labelled as untested. Write "ruled out X and Y; Z is a lead"
  rather than "the cause is Z" unless Z has been measured. When a cause is asserted in more
  than one place, the second copy is where it rots.

### The distinction that matters

Classes A, C and E are the dangerous ones, because their failure mode is *looking correct*. B
and D announce themselves eventually. So the ordering rule is: **prove the check can fail
before believing what it says.** That is the only one of these that generalises to mistakes
not yet made.

E is the worst of the three and the most recently learned. A and C produce a check that cannot
fail; E produces a *belief* that cannot fail, because nothing in the repository is capable of
disagreeing with a sentence.

## What each source publishes versus what we read (audited 2026-08-06)

Auditing a parser against its *own output* cannot find a field it never
looked at. Comparing the stored payload's keys against what the adapter does
found derivative holdings being dropped from every N-PORT filing — and,
through that, the `cusip:N/A` subject collapse.

**Two attempts at counting the gap were both wrong, and the correction
matters more than the count.**

- Counting *facts written* per source said `dtcc-sdr` read 4 of 110 columns.
  It reads twenty: lifecycle filters, block-trade cap flags, day counts and
  frequencies all inform the output without becoming facts of their own.
- Counting *string literals naming keys* fixed that but broke the other way.
  `edgar-companyfacts` scored 9 of 816 because it iterates XBRL concepts
  generically rather than naming them; the same is true of `fred`.

So a static count cannot distinguish "ignored" from "handled generically",
and a table of such counts reads as an inventory of neglect when it is
partly an artefact of the measurement. The numbers are not reproduced here
for that reason. What follows is what was checked by reading the code.

**`dtcc-sdr` — the swap tape is handled carefully, and is narrower than it
looks.** It already filters to `NEWT`/`TRAD` so amendments and novations
cannot double-count a trade, drops prints later flagged `EROR` by
identifier, excludes forward-starting trades whose par rate is not a spot
one, and flags CFTC block-trade and large-notional caps. What it genuinely
does not read: option premiums and strike prices (swaptions), the `Cleared`
and mandatory-clearing flags, package transactions, and the platform
identifier. Those are products and attributes this system does not model
yet, not oversights — but §12.1 lists swaptions, and this is where their
market data would come from.

**`sec-nport` — the fund-level block is unread.** Per-holding data is now
covered including derivatives; the filing's own `mon1..mon3` monthly return
and flow series, and the borrowings schedule, are not. Those are the fund's
numbers rather than an instrument's, and nothing in this system yet has a
fund as a subject.

**Rule:** when adding an adapter, record what of the payload it ignores —
auditing what a parser produces cannot reveal what it never read. And when
quantifying that, say what the count measures: "fields not written" and
"fields not read" differ by everything an adapter consults to decide.

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
- [ ] **Mutation testing — NOT ACHIEVED with mutmut 3.x. Do not sink more time into it
      without changing tool.** Five attempts on 2026-07-27, each hitting a distinct collision
      with this project's *own* verification machinery:
      1. the I3 registry walk — mutmut's synthesised `x__fn__mutmut_N` functions look like
         unregistered public analytics;
      2. `test_log_has_no_mutation_api` — synthesised methods look like mutation API;
      3. the store-protocol reflection test — same cause;
      4. the coverage floor — mutant-expanded source is ~74k statements, reads 13.6%;
      5. Hypothesis `HealthCheck.differing_executors` — mutmut runs tests from a different
         executor than Hypothesis expects.
      Root cause: this codebase enforces invariants by **runtime reflection** plus a coverage
      gate, and mutmut works by **synthesising code at runtime**. They are structurally at
      odds. Fixes 1–3 were applied and kept (invariant tests now ignore `__mutmut_` names —
      narrow, and no real member can carry that marker), but 4–5 need tool-level changes.
      **Recommended next attempt:** score mutation coverage only on the pure numerical
      modules (`hagan_west.py`, `bonds/pricing.py`, `bonds/callable.py`) — highest value (a
      survivor there means a wrong *number*), no reflection involved — and consider
      `cosmic-ray` instead. `[tool.mutmut]` is already narrowed to that scope.
      `mutants/` is gitignored (committed by mistake once, removed in a2bd7f7).
      **Retried automatically:** the nightly `deep` workflow attempts it every night as a
      `continue-on-error` step (Jack's instruction, 2026-07-27 — this is a genuine issue, not
      one to drop). A green step there means the blocker has lifted; promote it to required
      and record the kill rate at that point.
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

## A units error in `bonds.g_spread` (2026-08-11)

Found while building the government curve `SPRD` needs. **A ten-year par
Treasury priced at 100 on the very curve it was built from reported a
G-spread of +5.38bp.** A bond on the curve is worth the curve; the answer
should be zero.

`yield_from_price` returns a yield compounded at the bond's frequency.
`Curve.zero` returns a *continuously* compounded rate, because that is what
`exp(-zt)` discounting needs. `g_spread` subtracted one from the other.

Of the 5.38bp, **5.32bp was the conversion** — at 4.65%, semi-annual and
continuous differ by exactly that. It is systematic and always the same
sign, so it never reads as noise, and on a typical 100bp corporate spread it
is a 5% error.

**The golden tests could not have caught it.** They compare `g_spread`
against values computed the same mixed way, so a units error shared by both
sides passes. What catches it is a self-consistency property — a bond *on*
the curve has no spread — which needs no external reference at all. That
test is now in the suite, mutation-verified against the original
subtraction, and paired with a second assertion that the fix did not simply
zero everything: a bond with a coupon 200bp higher still reads +200.05bp.

The curve moves to the bond rather than the reverse, because market
convention quotes a G-spread on the bond's own basis.

### Status of `SPRD` and `OAS1`

The **government curve is built and tested** — 400 usable days on the live
store, bootstrapping to 3.97% at 1y and 5.18% at 30y. Bills under a year are
excluded rather than approximated: Treasury quotes them on a discount basis,
and treating them as par bonds would misprice the short end, which is
exactly where a two-year corporate reads its G-spread.

The **per-bond rows and the two screens are not written**. `tapi/spreads.py`
is in `AWAITING_WIRING` with that reason. A `BondSpreads` container was
written for them and deleted again — a type nothing reads is the defect this
repository keeps finding, not a head start on one.

**`OAS1` is data-blocked and this is now measured rather than assumed.**
N-PORT publishes `maturityDt`, `couponKind`, `isPaidKind` and thirty other
fields, and **no call schedule** — the schema has none. Without one, OAS is
identically the Z-spread, so an `OAS1` built on this store would either
repeat `SPRD`'s Z column under a different heading or price a call schedule
somebody invented. The honest form is a sensitivity screen over a *stated*
call structure, labelled as an assumption in the way ADR-0003 already treats
the volatility parameter.

## Phase 1 expansion: `DDIS` (2026-08-11)

An issuer's maturity ladder, keyed on LEI. It is the first screen that needed
*both* of the last two days' work: a bond could not be addressed before ISIN
resolution, and its issuer could not be identified before the GLEIF mapping.

**It is a sample, not a census, and the screen says so in three places.**
Bloomberg's DDIS shows amount *outstanding*. This is built from N-PORT, which
reports what funds *hold* — so it sees only the part of an issuer's debt that
appears in a filing we have ingested. Worse, the held figure is not a sum
across funds: several filers hold the same bond and all write to one subject,
so a point-in-time read returns one filing's position (I2). The column is
named **HELD** everywhere it appears, never OUTSTANDING, and the METHOD tab
explains why it is not a total.

The ladder therefore leads with the **bond count**, which survives all of
that: whether an issuer has five bonds due inside three years is a fact about
the issuer, not about who holds them.

### Two defects found building it, both plausible-looking rather than loud

**The report date was chosen by the wrong count.** It counted an issuer's
holdings and *then* filtered to straight debt, so a day whose seven holdings
were every one a derivative beat a day with six bonds — and the ladder came
back empty for an issuer that plainly had one. This is the same defect as
`SWPM`'s basis tab choosing the newest day the discount/forecast *pair* built
on when it needed a third curve. **Choosing by a count that is not the count
that matters** now has three instances in this repository, and each one
produced an empty screen rather than an error.

**The coupon was read as a decimal.** `nport:annualizedRt` ranges 0 to 12.5
on the live store with a median of 4.625 — it is a rate in percent. Rendered
as a decimal the ladder showed 546% coupons, which looks like a data fault
and would be chased in the parser rather than in the units. The field is now
named `mean_coupon_pct`.

Both mutation-checked: reintroducing either fails the test written for it.

Live output, Barclays PLC on 2026-03-31: 5 bonds in 1-3y at a 5.16% average
coupon, 1 in 3-5y at 4.22%, $10.86m held, no exclusions.

## Phase 1 expansion: `ECO` (2026-08-11)

**Twenty-five ingested series had no screen.** Thirty-six FRED series are in
the store and refreshed daily; eleven are the DGS curve points `ICVS` draws.
The other twenty-five — inflation, labour, credit spreads, breakevens, the
2s10s slope, policy and overnight rates, equity levels, vol, FX — could not
be displayed anywhere. That is the mirror of the fault this repository named
as its most common in Phase 2, *working analytics nothing can display*, and
it had been sitting in the store the whole time.

`ECO` is a spec mnemonic (§7.4) and was in `KNOWN_MNEMONICS` all along. It
now ships with a MAIN tab and a METHOD tab, each with a conformance case.

Two columns carry the whole burden of not lying:

**Units.** `CPIAUCSL` is 332.568 and `UNRATE` is 4.1 — an index on a 1982-84
base and a percentage of the labour force. Bare numbers in one column invite
reading either as the other and nothing on screen would contradict it. Every
unit is FRED's own stated unit, written once in `tapi/macro.py`.

**The observation date.** These series do not share a clock. On 2026-08-11
the store held CPI for June, unemployment for July, VIX for the 7th and the
2s10s slope for the 10th — ten weeks across four rows of one table.

Staleness is judged per series against its own release frequency, for the
same reason `ingest.health` judges sources against their own cadence:
monthly CPI six weeks old is a routine publication lag, and a daily series
six weeks old is a dead feed. One threshold would call the first broken —
and a warning that is always on stops being read.

Series in the catalogue the store has never ingested are reported rather
than dropped: a configuration gap and a series with no print today render
identically if the row disappears, and only one is worth acting on. FRED's
`.` for a non-publishing day is skipped rather than read as zero, which on
`VIXCLS` would be a volatility of nothing and a change equal to the level.

### The unbuilt-mnemonic count, for context

142 mnemonics in `KNOWN_MNEMONICS`, 17 built. The remaining 125 are mostly
Phase 3+ scope — `EMS`, `RFQ`, `TRADE`, `VCON`, `MSG`, `BOLT`, `DESK` are
trading and messaging. The Phase 1 candidates that are *feedable from data
already held* are `DDIS` (debt distribution by maturity, now reachable via
`gleif:lei`), `SPRD`/`OAS1` (the bond spread analytics are built and
validated but only `YAS` surfaces them), and `RELS` (related securities via
the GLEIF graph). None are blocked on data.

## GLEIF ISIN-to-LEI mapping (2026-08-10)

Added, and it is **not** redundant with N-PORT — the difference is the point.

N-PORT already carries an issuer LEI for every bond in the store, but that
LEI is *filer-reported*: a fund administrator recording what it believes the
issuer to be. GLEIF's is the issuer's own registration. Measured on the live
store:

| | |
|---|---|
| published mapping rows | 9,118,616 |
| our ISINs found in it | 1,175 of 1,861 (63%) |
| overlapping with an N-PORT LEI | 1,163 |
| **agree** | 1,148 |
| **disagree** | **15 (1.3%)** |

The disagreements are not noise. Three are bonds a filer attributed to
Deutsche Bank AG's LEI while GLEIF assigns them elsewhere — the classic shape
of a filer naming the parent or guarantor rather than the subsidiary that
issued the paper. **An issuer curve is fitted across one entity's debt, so
each of those was a bond on the wrong credit** — and the fit still succeeded,
still looked smooth, and still produced a rich/cheap call. `issuer_curves.py`
now prefers the registry over the filer.

Both facts are kept. GLEIF's is written under `gleif:lei` rather than
overwriting `nport:lei`, because a disagreement between a registry and a
filer is evidence *about the filing*, and an ingest that replaced one with
the other would destroy the signal worth having (I1, I2).

Only requested ISINs are parsed — 9.1M rows against a store holding 1,861
bonds. The raw payload is stored whole, so I5 replay is exact; it is the
*fact* set that is scoped, not the evidence. `treble refresh` supplies the
ISIN list from the store, so it stays scoped to the universe without anyone
maintaining a list.

CC0, keyless, `mapping.gleif.org/robots.txt` is a bare `Disallow:`.

## Binding scan (2026-08-10)

Ran every one of the 27 screen bindings against the live store and asked
which refused. **22 live, 5 refusing, 0 broken.** Three of the five refusals
were real defects rather than honest absences, and one of them was large.

### The bond universe was unaddressable

`sys:entity_owners` said "carries no LEI" for every equity — true, and
chasing it found something bigger. Bond resolution accepted **CUSIPs only**,
while N-PORT (the source of nearly all of them) publishes **ISINs**. The
live store holds **1,861 ISIN subjects against 147 CUSIPs**, so 93% of the
bond universe was addressable only by an identifier no source in the system
writes — and the **373,125-fact GLEIF relationship graph** behind those
issuers could not be reached from any screen at all.

Both directions now resolve, because a US or Canadian ISIN carries its CUSIP
in characters 3–11: an ISIN finds the bond, and a bare CUSIP finds a bond
stored under its ISIN. The check digit is verified rather than assumed — a
twelve-character typo is otherwise indistinguishable from an ISIN and
resolves to a subject with no facts, rendering as "no data for this bond"
instead of "you mistyped it". Validated against published ISINs (Apple
`US0378331005`, IBM `US4592001014`), not against itself.

This also caught the fixture builder writing `isin:US{i:010d}` — the right
*shape*, failing validation. A fixture built from those could not exercise
identifier resolution at all, which is exactly the defect the builder is
used to test for.

### "Most recent" is not "most complete", again

`sys:swpm_basis` reported "no EUR-EURIBOR-3M curve on 2026-08-07". There
*was* one — with 4 tenors against a `MIN_NODES` of 5. The shared market
picks the newest day the **discount/forecast pair** builds on; the basis tab
needs a **third** curve, and on a thin day the newest date is reliably the
worst one. Two days earlier the 3M curve had nine nodes.

**TVAL learned this exact lesson and it was not carried across** — its
issuer-curve default moved from "most recent report date" to "the date with
the most fittable issuers" for the same reason. The basis tab now builds its
own market with the short curve required, and *states* which date it used
when that is not the screen's date. It shows a real term structure: 11.16bp
at 4Y declining to 3.15bp at 20Y, annuity ratio flat at ~0.977.

### Half of ALLQ was correct-when-empty

The composites pane reported "Contributors 0 / Last live never". The quote
pane returned **zero rows** — a blank pane indistinguishable from one that
failed to load. The criterion is `ALLQ` correct-when-*empty*, and the
screen was getting it half right.

## Source sustainability sweep (2026-08-09)

Asked of Phase 1: which sources break, and would we notice. The answer to the
second was **no**, and that mattered more than any individual source.

**`treble status` counted payloads, and a count can only go up.** A source that
stopped publishing, changed its URL, or had its free tier withdrawn rendered
exactly like a healthy one — the number simply stopped changing, on a screen
nobody diffed against last week's. Every adapter now declares
`expected_cadence_days`, `ingest.health` compares that against the log, and
`treble status` reports it worst-first. Run on the live install it immediately
found **four sources silently stale**: `ecb-fx` and `coinbase` at 9 days,
`fred` and `dtcc-sdr` at 7, against declared daily cadences.

`treble refresh` is the other half: it re-runs the keyless feeds that are
overdue and skips the ones that are fine. It now covers **every** keyless
source — Treasury, ECB FX, ECB HICP, FRED, Coinbase, DTCC — and works out
*what* to fetch by reading the store rather than a config file: the FRED
series and Coinbase products it refreshes are whichever ones the store
already holds. Nobody has to remember which series they set up eighteen
months ago, which is the difference between a workstation you repair and one
you rebuild. First full run: 7,275 FRED facts, 24,439 DTCC, 2,100 Coinbase.
The only source left outside it is `twelvedata`, correctly — it needs a key,
and a keyless command should not pretend otherwise. Health says what broke, refresh
fixes it, and neither needs a credential. That is the answer to "will I have
to rebuild this in six months" — the failure gets surfaced on the day it
happens rather than the day a chart looks wrong.

**Three quiet states are distinguished** because they need different responses
and render identically if collapsed: `NEVER` (built but never wired into a
run), `OVERDUE` (was flowing, stopped), `IRREGULAR` (a lookup, not a feed — no
cadence declared, so staleness is not judged). Inventing a cadence for OpenFIGI
would produce a permanent false alarm, and a report that cries wolf gets turned
off, which is worse than not having one.

### What the sweep found on the live install

| Finding | Detail |
|---|---|
| `ecb-hicp` never once run | The adapter shipped, the inflation pricer shipped, and the store held **zero** `inflation:` subjects |
| `SWPM` claimed **"priceable — HICP stored"** | A hardcoded string, on a store with no HICP. Class E: an explanation recorded as fact, never tested — rendered to a user as a claim about their own data |
| CMT curve routed through a chart-download URL | `ICVS` reads Treasury data **via FRED's `fredgraph.csv`**, which exists to serve a web page's download button |
| 4 sources silently stale | Invisible before this sweep, because the only signal was a count |

The product catalogue now **asks the store** rather than asserting. Every row
is an existence probe, so the status changes when the data does — proven by
ingesting HICP and watching `INFLATION ZC` flip from "not priceable — no
inflation:EUR:HICP index ingested" to "priceable — HICP stored" with no code
change.

### Sources added

| Source | Why it is durable |
|---|---|
| **US Treasury Daily Par Yield Curve (CMT)** | Public domain (17 USC 105) — no licence to withdraw. No API key — no credential to rotate, expire or leak. `robots.txt` clean. Published every business day **since 1990**. 14 tenors against FRED's 11, adding 6W, 2M and 4M at the short end |
| **ECB HICP** | Was built and never run; now ingested (708 facts) and on the refresh rotation |

The Treasury curve also removes an intermediary. The chain was
Treasury → FRED → an undocumented URL → us; it is now Treasury → us, with the
FRED route still present as an independent second path to the same curve.

### Sources assessed and rejected, with the reason

| Source | Outcome |
|---|---|
| **Bank of England IADB** | `robots.txt` says **`Disallow: /boeapps/iadb`** — the exact path. The request *worked*; the policy forbids it. Not automated |
| **Bundesbank SDMX** | API alive, series key not yet identified. Open, not blocked |
| **Bank of Canada Valet** | **Permitted** — documented API, attribution required, rate limits must not be circumvented. The strongest untaken candidate |

**The BoE case is the KBRA lesson arriving from the other direction.** KBRA's
`robots.txt` said yes and its terms said no; BoE's endpoint answered 200 and
its `robots.txt` said no. Neither the response code nor any single document is
the authority. Fetching successfully is not permission.

### Remaining fragility, ranked

1. **`twelvedata` is a single point of failure for equity prices** — keyed, free-tier, redistribution-restricted. No second path exists. Bank of Canada and Bundesbank do not cover equities; the permissive options assessed so far all fail on terms.
2. **`fred` reads an undocumented endpoint.** Its documented API needs a free key. Now partly de-risked for rates by the direct Treasury route.
3. **`dtcc-sdr` fetches from a vendor S3 bucket name** (`kgc0418-tdw-data-0`) with terms that remain unverifiable behind Cloudflare.
4. **`trace-api` depends on Jack's OAuth credentials**, whose client id was exposed in a screenshot and is still unrotated.

## Data access findings (settled 2026-07-26 — do not re-derive)

**Credit ratings: closed across every NRSRO whose terms can be read (measured 2026-08-09).**
SEC Rule 17g-7(b) makes each NRSRO's full rating history legally public, free and XBRL —
so availability was never the obstacle, and the SEC hosts no mirror, only a taxonomy and a
publication guide. The obstacle is per-vendor terms, and all three smaller NRSROs previously
recorded as "unread rather than known-prohibitive" have now been read:

| NRSRO | File | Outcome |
|---|---|---|
| Egan-Jones | `egan-jones.io/17g-7` | host returns **403 to every non-browser client, including `robots.txt`** — the DTCC/Stooq line; not circumvented |
| KBRA | two XBRL histories linked from `kbra.com/regulatory` (`/documents/report/2390`, `2392`) | Terms of Use prohibit "any robot, spider, scraper, crawler, data extraction tool, automated script, bot, or other automated means", and separately forbid storing content "in a database" |
| Morningstar DBRS | `dbrs.morningstar.com/regulatory` | Terms prohibit "any automated device, program, tool, algorithm, process, or methodology … to access, acquire, copy, or monitor any portion of the Site" |

**KBRA is the one to remember: `robots.txt` said yes and the terms said no.** It serves
`User-agent: * / Allow: /` — an explicit, machine-readable grant — while its Terms of Use forbid
precisely the access that grant appears to give. A permission check that read `robots.txt` alone
would have returned "permitted", and would have been confidently wrong. **`robots.txt` states
what a crawler may fetch; it is not the licence.** Read the terms — a machine-readable "yes" from
the wrong document is worse than no answer, because it is actionable.

This closes the rating dimension of TVAL's similarity metric as a *measured* negative rather than
an open task, alongside seniority (0 of 460 N-PORT debt titles carry a seniority token; the
schema has no such field). Both now say so on the METHOD tab.

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

### 2026-08-03 (final) — gRPC, and `ALLQ` closes

Five services over `proto/tapi.proto` — refdata, flds, instruments, contrib, tql — answering from
the same `LocalTapi` as the HTTP and Flight transports, so a client choosing gRPC gets the same
answer to the same question.

**The stubs are generated by `make proto` and never committed.** A committed stub can silently
diverge from the proto beside it: it keeps compiling and keeps serving a schema nobody declared,
while the `.proto` reads as the contract. `make check` depends on `make proto`, so a clean
checkout always builds from the proto — and `TestTheGeneratedCodeMatchesTheProto` asserts the
generated services *and RPCs* match its declarations. Verified to fail: renaming `service Tql` in
the proto without regenerating trips both tests.

Two places where proto3's defaults would have lied, both handled:
`FIRMNESS_UNSPECIFIED` exists because proto3 forces a zero value on every enum, and the server
**rejects** it rather than picking a side — defaulting either way is wrong half the time. And
`has_bid`/`has_value` booleans carry the distinction proto3 scalars cannot, between an absent
double and 0.0. A missing figure arriving as zero is the failure this whole system exists to
prevent.

`as_of` is required and must be timezone-aware on the wire (I2). A server that filled in its own
clock would answer one question two ways and neither client would know.

**`ALLQ` reaches 1.0.** Both named halves — correct-when-empty and a complete contribution API —
now exist over every transport this phase defines. The remaining limitation is the loopback
binding, which is §22.1's entitlement model, which no phase before Phase 5 federation requires:
out of scope rather than missing.

### 2026-08-03 (last) — the suite was slow because *ingest* was slow

`make gate` went from **1003s to 52s** — same command, clean runs both sides. The cause was not
the test suite.

`DuckStore.write_facts` inserted one row at a time in a Python loop: 37,540 separate `execute()`
calls for a single EDGAR companyfacts payload, each a full round trip through DuckDB's parser.
Rewritten to build one Arrow batch and issue one `INSERT`: **11.20s -> 0.17s, 65x**.

The investigation is worth keeping because the first two hypotheses were both wrong:

| hypothesis | measurement | verdict |
|---|---|---|
| coverage instrumentation | no-coverage run equally slow | wrong |
| ~900 individually slow tests | every slow entry was `setup`, not `call` | wrong |
| an un-scoped fixture | true, but the *symptom* | incomplete |
| the fixture's write path | parse 0.24s, write 12.20s | **cause** |

Session-scoping the fixture — the fix the symptom pointed at — would have made the suite fast,
left **every `treble populate` exactly as slow**, and made the tests share mutable state. The
8.1M-fact store was built through that loop.

Two traps in the rewrite, both guarded: the Arrow schema is **explicit**, because that EDGAR
payload is 100% numeric and Arrow would infer the null type for `value_text` and then fail or
coerce; and the INSERT **names its columns**, so a later migration adding one cannot shift every
value a place to the left. All five value kinds verified to round-trip with exact types.

`write_provenance` has the same shape and was **left alone on the measurement**: 277 records
against 8,107,326 facts, 29,268:1. The reason is now a comment there, so it is not "fixed" later
on the shape of its neighbour's bug.

### 2026-08-03 (later) — the bulk-export guard, and Arrow Flight on top of it

**A flag declared thirteen times and read by nothing.** `SourceMeta.redistribution_restricted`
had been set on every adapter since Phase 1, its own docstring saying it "drives the bulk-export
guard". There was no bulk-export guard. The only read anywhere in the repo was a test written the
day before, asserting DTCC's flag was `True` — which tests a declaration, not a behaviour.

**That is the fourth mechanism found declared and switched off**, after three adapters that had
never run, an import contract with no test protecting its own config, and a drift check whose
failing cases I deleted rather than fixed. The pattern is consistent enough to be worth naming:
every one was a mechanism whose *existence* was verified by something, and whose *effect* was
verified by nothing.

So the guard was built before the transport, not after. `treble/ingest/registry.py` discovers
sources by walking the package — a hand-maintained list of restricted sources would be the same
defect in a new place, since an adapter added without an entry would export freely and nothing
would say so. `treble/tapi/export.py` withholds restricted-source facts, refuses licensed
identifier namespaces per §9.3 (*"resolution and display work; bulk export of a CUSIP master does
not"*), and **reports what it withheld** — a warehouse that received 90% of a universe and
believed it had all of it would compute coverage and risk aggregates against a hole it could not
see. 5/5 mutations killed, including one that stops the guard consulting the registry at all.

**And the guard's own arrival tripped a check — correctly.** `treble/ingest/registry.py` failed
`test_adapter_fixture_coverage`, which required every module under `treble/ingest/` to have a
recorded-fixture test. The registry defines no adapter and parses no payload, so it has no
fixture to read. The convenient fix was a fourth name in the check's hand-maintained
`_NOT_ADAPTERS` set — and that is the same move that once "fixed" a drift check by deleting the
two adapters it was failing on. Instead the check now decides adapter-hood by whether a module
*defines a `SourceAdapter` subclass*, so the list maintains itself. Verified both ways: the same
eleven adapters are still checked, and a probe adapter added without a fixture test still fails
it.

On the live store: `fred` exports 7,341 rows complete; `swap` exports **0 rows with 3,164 DTCC
facts withheld**; `cusip` and `isin` are refused outright. The withholding travels in the Arrow
table's schema metadata, so it is still there after the client persists the table.

**Arrow Flight** (`treble/tapi/flight.py`) then serves guarded exports with a fixed schema — one
inferred from whatever rows happened to be present would change shape between two pulls and a
warehouse would see a column appear as a data event. Loopback-only, like the HTTP server and for
the same reason: §22.1's entitlement model does not exist, so a routable Flight server would be
an unauthenticated bulk data tap.

### 2026-08-03 — `TVAL` v1: an evaluated price you can argue with

`treble/analytics/tval/evaluate.py`, 31 tests. Prong 1 of spec §15: direct observations weighted
by recency, firmness, size and corroboration, producing a price, a 1–10 score, an ASC 820 level,
and the derivation that made it.

**The weighting function is a parameter, not a constant.** §15.3 requires a user to be able to
run the same machinery with their own weights and get their own valuation; baked-in weights
would make independent price verification impossible by construction. `Weighting` is a frozen
model with a content hash, stamped into the I3 envelope, so two prices computed under different
weights are never indistinguishable after the fact.

**The score's four drivers are published separately** — corroboration, timeliness, firmness,
agreement — because "why is this a 4" is the question a valuation committee asks. A test asserts
the headline price is *reproducible from the drill-down rows*, which is the strongest form of
transparency available: a reader can recompute the number from what is shown beneath it.

**Two judgement calls, both recorded as tunable policy rather than hidden constants.** Stale
marks are Level 3 however well they agree — the first cut classified three funds agreeing to the
cent at a quarter end as Level 2 on a timeliness of 0.01, and past a freshness floor the price
rests on the assumption that nothing has changed, which is what Level 3 means. And **Level 1 is
absent from the enum** rather than an unreachable branch: it means an unadjusted quoted price in
an active market for the identical asset, and if one existed there would be nothing to evaluate.

**A mistake worth recording.** The first validation run against real N-PORT data printed prices
of 8371.0 and 16966.0. Those were equities, and the throwaway query had applied the ×100 par
convention to share prices — exactly what `AssetCategory` exists to prevent, as its docstring
says in as many words. Re-run through the real `implied_price` model: 182.18, 169.66, 250.73,
three independent filers, **0.000bp** range. The error was in the query, not the code, and it is
a live demonstration that the guard is load-bearing.

**One honest fact about the data:** only *equities* have three-or-more-filer corroboration in
this store. No bond does. N-PORT corroboration for bonds is thinner than the Phase 1 finding
suggests, and the score reflects that rather than papering over it.

### 2026-08-02 (last) — `ALLQ` and the contribution API

Thirteen screens. `allq.screen.yaml`, `treble/tapi/contribution.py`, `POST /contribute`, and two
conformance cases — one for a quoted book, one for the empty book that is this install's actual
state.

**Firmness became required, with no default.** A quote is either indicative (an opinion) or
executable (a commitment), and either default would be a lie half the time. Making it required
broke eight existing tests, which is the mechanism working: every construction site now has to
say which. **`TGN` and `TCMP` are separate composites** — TCMP over executable quotes only,
because a composite that blended indicative levels into a price labelled executable would be the
most dangerous number on the screen.

**The contribution API is the only write path in this system.** Everything else reads public
filings; this is where a human asserts a price. So it refuses anonymous, one-sided, crossed,
non-positive, zero-size and future-dated quotes, each with the reason in a 400 — a contributor
whose quote silently vanished would keep sending it and believe it was live. A locked market
(bid == ask) is explicitly *accepted*, with a test, so the crossed-quote refusal cannot swallow
a real one.

**Quotes are deliberately not facts.** They live in memory and expire. A fact stays true of the
moment it described; a quote is an offer that dies in minutes, and writing them to the store
would make the fact table a place where things stop being true — exactly what I2 forbids.

`TapiView` stays read-only: the contribution service is a separate parameter to `create_app`
rather than a method on the protocol, so no resolver can publish a quote (I7).

### 2026-08-02 (later still) — the `SWPM` screen

Twelve screens now. `swpm.screen.yaml` + `treble/tapi/swap_market.py` + three conformance cases
(one per tab). The screen is pane-driven rather than security-keyed, like `ICVS`, because a swap
trade is not in the security master.

`build_swap_market` assembles the CurveSet from stored `swap:*` facts and refuses two things
that would each produce a plausible screen: **curves from different trading days** (a front end
from today and a long end from last Tuesday bootstraps fine and is wrong), and **forecast nodes
past the discount curve's last node** (extrapolated discounting does not announce itself). Both
have tests; neither failure mode raises on its own.

Verified on the live store: PV −0.00 on a par swap, DV01 EUR 85,861, annuity EUR 859m, 10 annual
fixed against 20 semiannual floating flows.

### 2026-08-02 (later) — the EUR forecast curve: `SWPM` works end to end on real prints

`EUR-ESTR-OIS` discounting against `EUR-EURIBOR-6M` forecasting, both from the same DTCC file.
This is what the criterion actually needed: an OIS curve discounts but cannot project, so until
a term index existed the multi-curve pricer had real data for only half of itself.

**The index tenor had to come from the floating leg.** `UPI Underlier Name` is `EUR-EURIBOR` for
both 3M and 6M swaps — nothing in the index name separates them. Only the reset frequency does,
and merging them blends two curves a tenor basis separates by ~11bp.

**One normalisation earned its place.** The file spells an annual leg as both `YEAR`x1 and
`MNTH`x12. Comparing the raw pair dropped 11% of the ESTR prints and 5% of the SOFR prints for no
economic reason — an exclusion nobody would see, because the curve left behind still looks
complete. `frequency_months` normalises to months.

**Verified on real data:** a trade matching a curve input reprices to its quote at **0.0000bp**
through the independent cash-flow pricer, and the single-curve telescoping identity is broken by
**EUR 2,110,467 on EUR 100m**.

**Mutation testing found one more dead test.** Every EURIBOR print in the real file accrues
ACT/360, so deleting the float day-count filter changed nothing and the mutation survived — the
fixture could not exercise it. That is not evidence the filter is unnecessary; it is evidence
real data alone cannot prove it. Fixed with an injection test, and the reason is written into the
test so nobody deletes it as redundant. **25/25 across all three mutation targets.**

The 15 stored days were re-derived by **replaying the recorded payloads** rather than re-fetching
— the raw bytes are content-addressed and already in the payload store (I5), so no new requests
went to DTCC. The re-parse wrote restatements rather than overwriting, which is I2 doing its job.

**Next:** the `SWPM` screen. Nothing is blocking it now.

### 2026-08-02 — the DTCC SDR adapter: a real USD swap curve

`treble/ingest/dtcc.py`. CFTC Part 43 public dissemination, ~20,000 interest-rate prints a day,
reduced to a **USD SOFR OIS curve 1Y–30Y**. Fifteen trading days ingested through the real
`treble populate` path; every input reprices to 1e-10 through the Phase 1 bootstrap.

**The terms could not be read, and Jack chose to proceed anyway.** `dtcc.com/legal.php` is behind
Cloudflare bot protection (HTTP 403 to any non-browser client); circumventing it was refused.
DTCC sells *OTC Direct Connect*, a paid systematic-access product for this same data. Both facts
were put to Jack before any code was written; he chose "build it anyway". Recorded in the adapter
docstring, in its `SourceMeta.licence`, and in Phase 2 above — three places, because a decision
like this must not survive only in a commit message.

**These are transacted rates, not quotes**, and that drives the design: median never mean (the
real 10Y prints ranged −0.50% to +0.70% around a 4.31% median), interquartile dispersion
published beside every node, and a tenor with fewer than three prints omitted rather than
published.

**Mutation testing found four dead tests, which is the point of running it.** The first version
of the filter tests removed a category of row by hand and compared to the unfiltered run — which
proves nothing when those rows were already excluded by some *other* filter. 4 of 8 mutations
survived. Rewritten as injections: clone a real print, change exactly one field, price it
absurdly. Two still survived because changing one *field* changed which *filter* fired (moving
the effective date shortened the tenor, so the tenor filter did the work). Fixed by moving dates
coherently. **Now 22/22 across all three mutation targets.** The harness also caught a
constructor change that had broken two fixtures — a failure the earlier green run could not have
shown, because it predated the change.

**Next:** the `SWPM` screen still needs a *forecast* curve. See Phase 2 above.

### 2026-08-01 — Phase 2 `SWPM`: the multi-curve, CSA-aware pricer

`treble/analytics/curves/multicurve.py`, `derivatives/csa.py`, `derivatives/swap.py`. A forecast
curve is bootstrapped against an *exogenous* discount curve; tenor basis swaps connect index
curves; a cross-currency-basis CSA discount curve is built off the overnight curve; the CSA
resolves to a named discount curve or refuses. DV01 and bucketed DV01 rebuild the curve set from
bumped market quotes rather than shifting solved zeros.

**Validation.** A trade written to match a curve input reprices to its quote to **under 0.01bp**,
through a code path that shares nothing with the bootstrap's residual function — two
implementations agreeing, not one agreeing with itself. The single-curve telescoping identity is
broken by **$2.76m on $100m** notional, and a companion test shows the same assertion *pass* when
the two curves are deliberately made one, so it is a check that can fail. `make mutation` now
covers `swap.py`: **12/12 killed** across both targets.

**Three mechanisms fired and were right.** (1) The pinned I4 hash rejected the `CurveConfig`
schema change and demanded ADR-0006 — which found a real defect on the way: the Phase 1 bootstrap
accrued both swap legs in one day count, so the curve repriced its inputs under its own
definition of them while a *market* swap came out 3.4bp off. Now fixed with per-leg conventions.
(2) The I3 registry walk caught the two curve builders. (3) Widening the I3 exclusion list is now
itself bounded by `test_the_exclusion_list_stays_within_its_stated_category`, verified to fail —
the list was the obvious way to delete the invariant instead of satisfying it, and that has
happened here before.

**Next:** the `SWPM` screen is blocked on a swap-curve data source (see Phase 2 above).

### 2026-07-29 — WP11: ICVS and YAS; the bond maths validated externally

Seven of eleven screens. ICVS renders the CMT par curve (eleven tenors, 1M to 30Y); YAS shows
published bond terms plus computed yield, modified duration, convexity, DV01 and workout date.

**The analytics reproduce the US Treasury's own auction yields from their own prices to within
0.07 bp** (mean 0.017 bp) across 46 nominal coupon auctions — external validation, since
Treasury computes those independently. Pinned offline in
`tests/analytics/bonds/test_treasury_auction_goldens.py`.

Field mnemonics (`YLD_YTM_MID`, `DUR_ADJ_MID`, `CNVX_MID`, `DV01`, `WORKOUT_DT_MID`) were
confirmed by Jack, not chosen here: CLAUDE.md forbids coining mnemonics, and every mnemonic
the spec names is a Bloomberg field verbatim, so these continue that vocabulary.

Defects found, all class A/C — things that produced plausible output rather than errors:

1. **Modified duration wrong by three orders of magnitude.** The risk measures take a *yield*;
   passing the clean price does not raise, because QuantLib reads 98.88 as a 9888% yield and
   returns 0.0063 for a twenty-year bond. Now the yield is computed once and fed in; DV01 is
   cross-checked against duration x price / 10,000 in the tests, which ties two independently
   computed measures together so a unit error in either shows up off-screen.

2. **TIPS were indistinguishable from nominal bonds in the store.** Treasury publishes
   inflation-indexed notes under the same "Note"/"Bond" types; only
   `inflation_index_security` separates them and the adapter did not capture it. Priced as
   nominal, a 5-Year TIPS returns a 1.32% real yield that sits beside 4% nominals looking
   entirely plausible. Now ingested, and YAS refuses to compute for one.

3. **My own validation harness was the bug, not the data.** It aggregated facts with `max()`
   per field, pairing the price from one auction with the yield from another on reopened
   CUSIPs, and reported 10-30 bp errors that did not exist. Worse, the first draft of the
   golden test wrote that false explanation into its docstring as though `dated_date` caused
   it. Corrected to the measured truth: `dated_date` improves the fit about threefold
   (0.07 bp against 0.20 bp). The store was right throughout — it keeps each auction
   separately, and every field of an auction shares `effective_from`, so latest-wins picks
   price and yield from the same auction.

4. **Every Govt/Corp ticker was treated as a CUSIP.** `IBM 4.15 05/15/39 Corp` is a valid
   reference whose ticker is "IBM"; it resolved to `cusip:IBM` and reported a missing
   instrument rather than an unbuilt lookup. Shape-checked now, with descriptor-based
   resolution left honestly unimplemented.

5. **An unwired model returned a silent null**, indistinguishable from missing data.
   `_WIRED_MODELS` makes it explicit: no data path raises, absent inputs return null.

6. **Two tests used YAS as their example of an unbuilt screen** and would have silently
   started asserting nothing the moment YAS shipped. They derive an outstanding mnemonic from
   the grammar now, and skip themselves when the last screen lands.

Also: table panes were misused for the curve and then removed. A curve is yield against
tenor; the only line-drawing pane type is `timeseries`, yield against *date*. Binding one to
the other would draw a correct-looking picture of the wrong thing, so ICVS ships as a table
until a pane type means what it shows.

### 2026-07-27 — WP11 batch 1: FA, SPTR, MDL, FLDS

Five of eleven screens now exist. `FA` is three tabs of as-reported XBRL (income, balance
sheet, cash flow); `SPTR`, `MDL` and `FLDS` are the screens whose subject is the workstation
itself — the provenance DAG, the model registry and the field dictionary.

**Accuracy check that came free.** IBM's balance sheet closes exactly on the rendered figures:
liabilities 117,558,000,000 + equity including non-controlling interests 34,541,000,000 =
assets 152,099,000,000. EPS ties too: 2,165,000,000 / 941,192,024 = 2.30 basic, and
/ 953,263,534 = 2.27 diluted, both matching the filed per-share tags. Nothing forces these to
agree — the tags are stored independently — so agreement is evidence the ingest is faithful.

Three defects found by looking at output rather than trusting green tests:

1. **`MDL` would have rendered an empty registry.** Models register as a side effect of
   `@model` at import time, and nothing imported the analytics submodules, so
   `MODEL_REGISTRY` read `{}`. An empty table reads as "this system has no models" rather
   than "nothing has been imported". `load_all_models()` now walks the package explicitly;
   all 15 models appear.

2. **`SPTR` returned zero rows for a company with 345,326 facts.** The traversal iterated the
   field *dictionary*, which documents six mnemonics; every real IBM value lives under an
   as-reported XBRL tag that the dictionary resolves dynamically and cannot enumerate. Now
   `DuckStore.subject_provenance()` asks the store what provenance it actually holds, point-in-
   time like every other read. It returns the two EDGAR documents behind IBM.

3. **Table panes were not drawn at all.** `table_scroll` had no rendering, so all three new
   screens resolved correct data, passed conformance — which asserts a pane's region and
   binding and never its pixels — and displayed an empty box. Correct data, green suite, blank
   screen. Both renderers now draw tables, truncation is always announced, and
   `tests/render/test_table_panes.py` pins it.

Conformance cases for shipped screens now **reference** the real definition rather than
carrying a copy, so a case cannot keep passing against a screen that changed underneath it.
A new meta-test fails if any shipped screen has no case at all — a screen nothing is checked
against can render differently on the two surfaces and nothing would notice.

### 2026-07-27 — WP12: the desktop application, and four holes it exposed

`Treble Tracker.app` now opens from the Dock as a real macOS application. Building it required
the HTTP transport and a second renderer, and putting those in front of a real window found
four defects that every unit test had passed over. Each is now pinned by a test.

1. **The desktop client could not read a single response.** Its WebView runs on its own origin
   (`tauri://localhost`), so every call to loopback is cross-origin, and the server sent no
   CORS headers. The requests *succeeded* — the access log showed a wall of `GET /health` at
   200 OK — while the client threw away every reply and retried until it timed out. Invisible
   from the server side, which is why the regression test asserts the header rather than the
   status code. The allowlist is explicit, never `*`: loopback is reachable from any page the
   user has open, and a wildcard would let a website read this store.

2. **The store that opened depended on the working directory.** `DEFAULT_DATA_DIR` was a
   relative `Path("data")`. Launched from the Dock, or from a terminal anywhere but the repo
   root, the workstation silently created a *fresh empty store* and rendered a screen of
   dashes — indistinguishable from a company that reports nothing. It caught me during this
   session: a verification server built a second store at `~/.treble` and served a screenful
   of nothing at 200 OK while I read it as a passing check. Now anchored absolutely, with
   `TREBLE_DATA_DIR` to override, and `DuckStore.fact_count()` makes an empty store announce
   itself at startup instead of rendering plausible emptiness.

3. **Opening the application required EDGAR to be reachable.** Ticker resolution fetched
   `company_tickers.json` on every launch. A desktop app that cannot open on a train is
   broken. Now cached, refreshed when stale, and fallen back to when a refresh fails — the
   only unopenable state is "never once online", and it says so.

4. **The HTTP server sat in the wrong layer.** Written as `treble/tapi/server.py`, it imported
   `treble.render` and broke the layered contract. It resolves screens, which is a render-layer
   act, so it moved to `treble/render/server.py`. The contract caught a name that had been
   quietly asserting TAPI serves screens; it does not — it serves data, and this serves
   buffers resolved from it.

The layout-tree comparison is now structural rather than byte-wise (the text snapshot stays
character-exact). Node spells `1.0` as `1`, and a renderer in another language must not fail
for its runtime's number formatting when every position, string, attribute, pane region and
binding still matches exactly. `canonical_json()` is now the single serialisation point,
because the first cut of the web renderer drifted by reproducing `json.dumps` parameters by
hand and getting `ensure_ascii` wrong.

**Process note.** The first `tauri build` reported exit 0 and had failed: the command ended in
`... > log 2>&1; echo "EXIT=$?"; tail`, so the status came from `tail`. This is the seventh
instance of that exact mistake, and the first where the safeguard held — `scripts/gate.sh`
was not the thing that ran, so nothing caught it but reading the log. Builds are now run with
the exit code captured to a file before anything else touches the pipeline.

Gate green: 90.42% coverage, both import contracts kept, mypy --strict clean, three renderers
conformant on every case.

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
