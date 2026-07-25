# CLAUDE.md — Treble Tracker

Persistent constraints for this repository. Read at the start of every session. If anything here conflicts with something you remember from earlier in the conversation, **this file wins** — earlier context may have been compacted or may predate a decision recorded here.

**The specification is `docs/treble-tracker-spec.md`.** It is the contract. This file records how to implement it and the decisions that are already settled.

---

## 1. The seven invariants

These are structural properties the spec claims. Each must be enforced by a mechanism, not by discipline. **None of them can be retrofitted** — each one, added late, requires rewriting the storage layer or the analytics layer. Get them right in Phase 1.

### I1 — Provenance on every field (§5.4, §7.1 `SPTR`, §9.4)

Every value in the system traces to its source. This is not metadata bolted on; it is part of the value's type.

Every stored fact carries a `provenance_id` referencing a `provenance` table with: source system, source document URI, retrieval timestamp, extraction method, extractor version, confidence, and (where applicable) page/tag/XPath locator. Derived values carry a provenance record referencing their inputs, forming a DAG.

Enforcement: the storage layer rejects writes without provenance. `SPTR` is a generic traversal of the DAG, not a per-screen feature.

### I2 — Bitemporality and point-in-time correctness (§14.4, §20.3)

Every fact has **two** time dimensions:

- `effective_from` / `effective_to` — the period the fact describes
- `knowledge_from` / `knowledge_to` — when the system could first have known it

For fundamentals this means `period_end` **and** `filed_at`/`accepted_at` from the EDGAR acceptance timestamp. Every query carries an implicit or explicit `as_of` knowledge date. The default `as_of` is now; backtests and historical analysis pass a past date and **must** receive the world as it was known then.

Enforcement: the query layer takes `as_of` as a required parameter with a default, never as an optional filter callers might forget. Restatements create new rows; they never update old ones. A property test asserts that no query result contains a row with `knowledge_from > as_of`.

This is the single most common way financial systems produce wrong answers, and it is invisible until someone trades on a backtest.

### I3 — Model identity stamped on every analytic output (§7.10 `MDL`, §10.2)

No analytic returns a bare number. Every analytics call returns a result envelope containing the value, the model ID, the model version, the full parameter set, the input snapshot reference (including curve config hash), and the computation timestamp.

Enforcement: a `@model` decorator wraps every pricing and risk function, registers it in the model registry at import time, and constructs the envelope. A function producing analytics without the decorator fails a static check in CI.

### I4 — Curve configuration is content-addressed and stamped (§11.1)

A curve is defined by its instrument selection, interpolation method, convexity adjustment, and discounting basis. That configuration is hashed, stored, and referenced by every result computed from it.

Enforcement: `CurveConfig` is a frozen model with a stable content hash. Curve objects cannot be constructed without one. The hash appears in every I3 envelope.

### I5 — Deterministic replay (§8.2, §22.2)

Raw payloads are stored content-addressed and immutable. The ingest log is append-only. Every transformation is versioned. Any past system state is reconstructible by replaying the log to a point.

Enforcement: ingest adapters write raw bytes to the payload store *before* parsing. Parsers are pure functions of (raw payload, parser version). A replay test reconstructs a known past state and asserts equality.

### I6 — One screen definition, many renderers (§6.1)

See §4 below. Enforced by the renderer conformance suite.

### I7 — TAPI is the only data path (§8.3)

Every screen, every export, every client feature reads through TAPI. There is no privileged internal interface.

Enforcement: renderer and screen packages may not import storage or analytics packages directly. Enforced by an import-linter contract in CI.

---

## 2. Stack

Settled. Do not substitute without asking.

**Language and tooling**
- Python 3.12+, managed with `uv`. Not poetry, not pipenv, not bare pip.
- Rust (stable) for the Tauri client shell.
- TypeScript for the Tauri renderer.
- `ruff` (lint + format), `mypy --strict`, `pytest`, `hypothesis`, `import-linter`.
- Pre-commit hooks enforce all of the above. CI runs them on every push.

**Core**
- **DuckDB** + **Parquet** — analytical store, warm and cold tiers
- **Polars** — dataframe layer (not pandas; pandas only where a dependency forces it)
- **Pydantic v2** — every schema, every config, every message
- **PyArrow** — the interchange format across every boundary
- **QuantLib** (PyPI package `QuantLib`) — the analytics core. See §5 for critical usage constraints.
- **Lark** — the command grammar parser (§3)

**Analytics**
- CVXPY + Clarabel/OSQP (convex), HiGHS (MIP) — optimisation
- statsmodels, `arch`, linearmodels — econometrics
- scikit-learn, LightGBM — ML layers
- SciPy `qmc` — Sobol/Halton sequences

**Clients**
- **Textual** — TUI renderer
- **Tauri v2** — desktop shell
- **FastAPI** + WebSocket — local TAPI transport; **gRPC** + **Arrow Flight** added at Phase 2 for the server deployment path

**Later phases**
- matrix-nio (Phase 3), QuickFIX-Python (Phase 3), LanceDB + Tantivy (Phase 5), vLLM/Ollama (Phase 5)

**macOS specifics**
- Apple Silicon native. No Rosetta.
- Homebrew for system deps only (`brew install duckdb rust node`).
- Docker Desktop required only from Phase 3 (Synapse). Phases 1–2 run with no containers.

---

## 3. Repository layout

**Already scaffolded.** The directories below exist, each `__init__.py` names the spec section its package implements, and `pyproject.toml` / `.importlinter` / `Makefile` are configured to match. Add modules inside this structure; do not reorganise it without a decision record.

```
treble/
  core/          identifiers, security master, entity graph, provenance, bitemporal store
  ingest/        one module per source; each implements the SourceAdapter protocol
  store/         Store, HistoryStore, IngestLog protocols + DuckDB/Parquet implementations
  analytics/
    curves/      bootstrapping, interpolation, config hashing        (§11)
    bonds/       yield, spread, risk, OAS lattice                    (§10)
    mortgage/    prepayment model, CMO waterfalls                    (§10.3)
    credit/      CDS, hazard curves, DRSK                            (§13)
    equity/      valuation, estimates, screening                     (§14)
    vol/         surfaces, SABR, cube                                (§11.3)
    derivatives/ SWPM, DLIB, TPay compiler                           (§12)
    risk/        TFM3, PORT, attribution, optimiser                  (§16)
    tval/        evaluated pricing                                   (§15)
    registry.py  the @model decorator and MDL backing store          (I3)
  tql/           parser, planner, executor                           (§4.2)
  tapi/          service definitions, transports, client libraries   (§8.3)
  screens/       screen definitions (.screen.yaml) + resolvers       (§6, §7)
  render/
    contract/    schema, abstract layout tree, conformance suite     (I6)
    tui/         Textual renderer
    web/         shared TS renderer used by Tauri and browser
  cmd/           command grammar, parser, dispatcher                 (§5)
  ai/            retrieval, extraction, ASK                          (§20)
  apps/
    desktop/     Tauri shell
docs/
  treble-tracker-spec.md
tests/
  golden/        published reference values for analytics validation
  fixtures/      recorded source payloads for offline ingest tests
  conformance/   renderer conformance cases
PROGRESS.md
```

Rule: `screens/` and `render/` may import `tapi/` only. Enforced by import-linter (I7).

---

## 4. The screen definition contract (I6)

**Build this before either renderer.** See the kickoff prompt for why.

A screen definition is a `.screen.yaml` file plus a Python resolver. The definition declares:

- a **cell grid**: rows × columns, with regions
- **static cells**: literal text with attributes
- **bound cells**: a field reference, a format spec, and attributes that may be conditional on the value (green if positive, grey if stale, dotted-underline if model-derived — §6.3, §5.4)
- **input cells**: editable, with type and validation
- **link targets**: what `<GO>` on this row or cell does
- **pane regions**: rectangles delegated to a graphical renderer, with a pane type and data binding
- **tabs**: named sub-views

The resolver is `resolve(definition, context, as_of) -> CellBuffer`. It calls TAPI, never storage.

**The conformance suite** lives in `tests/conformance/`. Each case is a definition, a fixed context, a frozen TAPI response, and two golden artefacts: an abstract layout tree (JSON) and a text snapshot. Every renderer must reproduce both. A renderer that cannot express a definition fails the suite — it does not get a special case.

Graphical panes are the one place renderers legitimately differ: the TUI renders a sparkline or braille plot where the desktop renders a WebGL chart. The conformance suite asserts the pane's *region, type, and data binding*, not its pixels.

---

## 5. QuantLib: critical usage constraints

QuantLib is the analytics core and it has three traps that will silently corrupt results.

**Trap 1 — the global evaluation date.** `ql.Settings.instance().evaluationDate` is process-global mutable state. Concurrent valuations at different as-of dates will interfere, producing wrong numbers with no error. This directly threatens I2.

Required: all QuantLib access goes through a context manager that acquires a lock, sets the evaluation date, yields, and restores. No QuantLib call outside it. For parallel valuation across as-of dates, use process isolation, not threads. Wrap this once in `analytics/_ql.py` and never touch `Settings` elsewhere.

**Trap 2 — object lifetime.** QuantLib's Python bindings wrap C++ objects whose lifetimes are not always managed by Python's GC. Handles to term structures must be kept alive for as long as any instrument references them, or you get a crash or garbage results. Keep explicit references in the pricing context object.

**Trap 3 — construction cost.** Calendars, day counters, and schedules are expensive to construct and are constructed constantly if you are careless. Cache them keyed by their parameters. Profile before optimising elsewhere; this is usually the hot spot.

**Validation.** QuantLib is mature but not infallible, and our bindings usage can be wrong even when the library is right. Every analytic gets golden-value tests (§7).

---

## 6. Data source engineering notes

Practical constraints that will otherwise cost you a day each.

**SEC EDGAR**
- A descriptive `User-Agent` header with a contact email is **mandatory**. Requests without it are blocked, sometimes at the IP level.
- Rate limit: 10 requests/second. Implement a token-bucket limiter in the adapter base class, shared across all EDGAR endpoints.
- Prefer **bulk downloads** over crawling: `companyfacts.zip`, `submissions.zip`, and the quarterly Financial Statement Data Sets. Crawling per-company is slow and rude.
- Use the **XBRL `companyfacts` API** for fundamentals — it is tagged and structured. Do not parse filing HTML for numbers that exist in XBRL.
- The **`accepted` timestamp** from the submissions API is the knowledge date for I2. Not the period end, not the filing date on the cover page.
- Issuers use **extension tags** heavily. The standardisation mapping (§14.1) must handle unmapped tags explicitly — surface them, do not drop them.

**FINRA TRACE**
- Freely downloadable historical and academic/enhanced files carry a reporting lag. Real-time dissemination is a separate commercial path.
- Build the adapter as `TraceFileAdapter` (implemented, default on) and `TraceRealtimeAdapter` (interface defined, disabled by default, documented). Consistent with §23.1: the free path ships, the other is pluggable.
- TRACE prints carry dissemination caps on large trades — a $5MM+ IG print shows as "5MM+". Do not treat the cap as the actual size; carry a `size_capped` flag through to `TVAL` weighting.

**OpenFIGI**
- 25 requests/minute unauthenticated; 250/minute with a free API key; up to 100 mapping jobs per request.
- Batch aggressively and cache permanently — FIGIs never change (§9.3), so a local mapping cache never needs invalidation.

**MSRB EMMA / DTCC SDR / GLEIF / FRED**
- All have published rate limits or bulk endpoints. Same pattern: token-bucket limiter, bulk-preferred, recorded fixtures for tests.
- GLEIF publishes full concatenated files including Level 2 relationship records — take the bulk file, not the API, for the entity graph (§9.5).

**Every adapter**
- Implements `SourceAdapter`: `fetch() -> RawPayload`, `parse(RawPayload) -> list[Fact]`, with `parser_version`.
- Writes raw bytes to the content-addressed store before parsing (I5).
- Has a recorded-fixture test that runs offline in CI.
- Declares its licence terms and any redistribution restriction in metadata.

**Redistribution guard.** Fields sourced from restricted identifiers (CUSIP in particular — §9.3) are flagged in the field dictionary. Bulk export paths check the flag and refuse. Resolution and display are unaffected.

---

## 7. Testing

The credibility of this system is its correctness. Tests are not a chore here; they are the product.

**Golden-value tests.** Every analytic validates against published reference values before it counts as done:

- Bond math → Treasury auction results (published price/yield pairs), and worked examples from published fixed income texts
- Curve bootstrapping → the curve must reprice every input instrument to within 1e-10; assert this as a property, on every curve, always
- OAS → cross-check against an independent implementation (OpenGamma Strata via a fixture, or a hand-built lattice for simple cases)
- CDS → the ISDA Standard Model has published test cases; use them
- Option pricing → published Black-Scholes values, and put-call parity as a property
- TFM3 → factor returns must reconstruct asset returns to within specific risk; covariance must be positive semi-definite after every correction step

**Property tests** (Hypothesis) for invariants that must hold for all inputs:
- price → yield → price round-trips to within tolerance
- yield-to-worst ≤ every individual yield-to-call
- survival probability is monotonically non-increasing
- Z-spread ≥ OAS for a callable, ≤ OAS for a putable
- effective duration → 0 as maturity → 0
- no query returns a row with `knowledge_from > as_of` (I2)
- every analytics result carries a complete model envelope (I3)

**Conformance tests** for renderers (§4).

**Replay tests** for I5.

**No network in CI.** Every ingest test uses recorded fixtures.

---

## 8. Phase gates

Do not begin a phase until every criterion of the previous one passes in CI on a clean checkout.

### Phase 1 — research workstation

- [ ] All seven invariants have enforcement mechanisms, each with a test that fails if the mechanism is removed
- [ ] Screen definition schema, resolver contract, and conformance suite exist; both renderers pass it
- [ ] Command grammar parses every example in §5.1 plus a fuzz corpus; yellow-key namespace resolution correct
- [ ] EDGAR, FRED, Treasury, TRACE-file, OpenFIGI, GLEIF adapters, each with offline fixture tests
- [ ] Security master and entity graph populated for the configured universe subsets
- [ ] `DES`, `FA`, `GP`, `HP`, `YAS`, `ICVS`, `SRCH`, `EQS`, `FLDS`, `SPTR`, `MDL` working in both clients
- [ ] Curve bootstrapping repricing inputs to 1e-10 across all supported interpolation methods
- [ ] `YAS` golden-value tests passing against published references
- [ ] TAPI with Python client; `TDP`/`TDH`/`TDS` spreadsheet functions via xlwings
- [ ] TQL parses and executes the §4.2 example
- [ ] Local-only mode: one command from clean checkout to working workstation
- [ ] `PROGRESS.md` current

### Phase 2 — real-time, portfolio, risk

- [ ] Ticker plant with conflated display path and unconflated TPIPE path
- [ ] `ALLQ` correct-when-empty; contribution API complete
- [ ] `PORT` with TFM3 v1; factor model validation tests passing
- [ ] `TVAL` v1 with score and full transparency drill-down
- [ ] `CDSW` against ISDA published test cases
- [ ] `SWPM` with multi-curve CSA-aware discounting
- [ ] Canvas with FDC3 context propagation
- [ ] gRPC + Arrow Flight transports

### Phase 3 — communications, execution

- [ ] `IM` on Matrix with verified identity; local Synapse in Docker
- [ ] `PEOP`, `TVault` WORM archiving with retention tests
- [ ] `EMS` FIX connectivity against a simulator
- [ ] `PMS` compliance DSL: version-controlled, unit-testable rules
- [ ] `TCA`

### Phase 4 — advanced analytics

- [ ] `DLIB` with TPay compiler and AAD greeks
- [ ] `RISK` XVA
- [ ] Mortgage stack: prepayment model fitted to agency loan-level data, CMO waterfalls
- [ ] `TIDX`

### Phase 5 — AI, federation, mobile

- [ ] `ASK` with mandatory grounding and the numeric-substitution guard (§9 below)
- [ ] Published evaluation harness with results
- [ ] `TI` dashboards
- [ ] Node federation
- [ ] Mobile

---

## 9. AI layer rules (§20.3)

When Phase 5 arrives, these are non-negotiable:

**Numbers never come from the model.** The generation pipeline emits typed placeholder tokens (`{{tql:...}}`); a post-processor resolves each against TQL and substitutes the result. A numeric literal appearing in model output that did not come from substitution causes the response to be **rejected**, not corrected. Enforce with a validator that scans generated text for unresolved numerals.

**Grounding is mandatory.** Every claim carries a citation to a retrieved passage. Ungrounded claims are suppressed rather than emitted.

**Retrieval is `as_of`-filtered** (I2) and entitlement-filtered.

**The evaluation harness is published** with results, and is runnable by users against their own configuration.

---

## 10. Working conventions

- **Plan before building.** Plan mode for each phase and any task touching more than three files.
- **Commit per acceptance criterion**, referencing it in the message.
- **Update `PROGRESS.md`** every session: phase, criteria passed/outstanding, open questions, decisions with rationale. Write it for a reader who remembers nothing.
- **`NotImplementedError` with a spec section reference** for any unimplemented path. Never a silent stub, never a plausible wrong number.
- **No fabricated data** outside clearly-marked test fixtures.
- **No invented mnemonics or field names.** §24 and §9.6 are the contract. Ask if you need something new.
- **Ask when ambiguous.** Batch non-blocking questions; block on architectural ones.
- **Push back when the spec is wrong.** If something is unsound, incorrect, or unachievable as written, say so with reasoning. Changing the spec beats building something broken.

---

## 11. Failure modes specific to this domain

Things that will look fine and be wrong. Watch for them in review.

- **Look-ahead bias** from using period end instead of filing date (I2)
- **Survivorship bias** from universes that exclude delisted securities (§14.4)
- **Silent day-count or calendar errors** — wrong by a few basis points, invisible without golden tests
- **Curve interpolation artefacts** — cubic spline on zeros producing negative forwards (§11.1); default to monotone convex
- **Stale prices presented as live** — I1 provenance plus the grey/stale display convention (§6.3) exist to prevent exactly this
- **Modified vs. effective duration confusion** on callable bonds (§10.1)
- **Price return vs. total return confusion** (§14.5) — always explicit, never inferred
- **Dissemination caps read as actual size** (§6 above)
- **Conflated data used where unconflated is required** — VWAP and volume analytics off a conflated feed are wrong (§6.2)
- **QuantLib global evaluation date leakage** (§5 above)
- **Restatements overwriting history** instead of appending (I2)
