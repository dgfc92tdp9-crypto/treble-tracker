# START HERE

Your task, in full. Read this, then `docs/treble-tracker-spec.md`, then `CLAUDE.md`, then `PROGRESS.md`.

---

## Mission

Build **Treble Tracker**, a free and open institutional finance workstation, on macOS (Apple Silicon), exactly as specified in `docs/treble-tracker-spec.md`.

That document is the **contract**, not inspiration. Every function mnemonic, every field name, every model, every architectural property described in it is a requirement. Where the spec states a behaviour, implement that behaviour. Where the spec names a library, use that library. Where the spec makes a claim about a property of the system — reproducibility, provenance, point-in-time correctness — that property must be **mechanically enforced**, not merely documented.

Read the spec in full before writing any code. It is ~20,000 words and it repays complete reading: the sections interlock, and several requirements in §16 are only satisfiable because of decisions made in §10.

---

## Before you write any code

Enter plan mode and produce a written implementation plan covering:

1. **The seven invariants** (`CLAUDE.md` §1). For each, the specific mechanism that enforces it and the test that fails if the mechanism is removed. These cannot be retrofitted — get them wrong and the build fails at Phase 3 and restarts.
2. **The screen definition contract** — schema, resolver interface, conformance test approach. See below.
3. **Phase 1 breakdown** against the gate criteria in `CLAUDE.md` §8, mapped onto the package layout already scaffolded in `treble/`.
4. **Open questions.** Anything in the spec you find genuinely ambiguous, internally inconsistent, or technically unsound. Do not paper over these. Ask me.

Do not start Phase 1 until I have approved the plan.

---

## The hardest problem, and why it comes first

The spec (§6.1) requires that **one declarative screen definition drives three renderers** — Tauri desktop, Textual TUI, and web — and that adding a function does not mean writing three UIs.

Everything in the presentation layer depends on this, and it is impossible to retrofit. Build a Textual UI first and try to extract a shared abstraction later, and you will fail: the abstraction will have been shaped by Textual's widget model.

**Build the screen definition layer and its conformance suite before either renderer.** In order:

1. Screen schema as Pydantic models — cell grid, field bindings, conditional attributes, link targets, graphical pane regions, tabs.
2. Resolver contract: `resolve(definition, context, as_of) -> CellBuffer`. Calls TAPI, never storage.
3. **Renderer conformance suite** in `tests/conformance/` — screen definitions plus golden expected outputs, as an abstract layout tree and a text snapshot.
4. TUI renderer, against the suite.
5. Tauri renderer, against the **same** suite.
6. Only then real screens (`DES`, `FA`, `YAS`, …), each a definition plus a resolver, each working in both clients automatically.

A screen that needs renderer-specific code is a defect. Escalate it rather than special-casing it.

Graphical panes are the one legitimate difference: the TUI draws a sparkline where the desktop draws a WebGL chart. The suite asserts the pane's region, type, and data binding — not its pixels.

---

## Scope and sequencing

Build toward the complete spec. Work through the five phases in spec §23.3 **in order**, with hard gates between them (criteria in `CLAUDE.md` §8).

> **Do not begin phase N+1 until every acceptance criterion for phase N passes, in CI, on a clean checkout.**

This is not bureaucratic caution. This system's value is correctness — a bond analytics library that is 95% right is worse than useless, because the wrong 5% is invisible until it costs someone money. A phase left "mostly done" while you move on will rot silently.

Each phase must be independently useful and independently shippable. At the end of Phase 1 I should run one command and get a working research workstation for fixed income and fundamental equity analysis. Everything after is addition, never rework.

---

## Single-machine substitutions

The spec describes institutional infrastructure; you are building on one MacBook. The substitution table in `CLAUDE.md` §2 and §3 is **authorised and required**. The rule behind all of it:

> Substitute the *implementation*, never the *interface*. If someone later points this codebase at a real cluster, only config should change.

---

## Where the spec describes a network effect and you have one user

Three features depend on participation that does not exist yet. Do **not** silently no-op them, and do **not** fabricate data to fill them.

- **Contributed quote network** (§2.2, `ALLQ`) — build the full contribution API, schema, and quality scoring. With one user it is empty, so `ALLQ` renders public reported trades (TRACE, EMMA, SDR) plus any configured free-tier quotes, with a visible indication that the contributed tier is empty. **Correct-when-empty, not broken-when-empty.**
- **Consensus estimates** (§14.2) — prongs 1 and 2 will be sparse. Prong 3 (model-generated) is fully buildable now: build it, label it clearly as model-derived, score it against actuals on the leaderboard mechanism the spec describes.
- **Community research** (§17.4, `TI`) — build the dashboard format, the fork mechanism, and the local library. Ship a few reference dashboards yourself.

The *mechanism* is in scope and must be complete. Only the *population* is thin, and the UI says so honestly rather than presenting emptiness as an answer.

---

## Working agreement

**Plan, then build.** Plan mode before each phase, and before any task touching more than three files.

**Test as you go, not after.** Every analytic gets golden-value tests against published references before it counts as done. Every screen gets a conformance snapshot. Every ingest adapter gets a recorded-fixture test that runs offline. `CLAUDE.md` §7 lists the validation sources.

**Commit per acceptance criterion**, referencing the criterion in the message. Small, reviewable commits.

**Update `PROGRESS.md` every session** — phase, criteria passed and outstanding, open questions, decisions with rationale. Write it for a reader who remembers nothing, because after a context reset that reader is you.

**Record architectural decisions** in `docs/decisions/` as numbered records. Link them from `PROGRESS.md`; do not duplicate their content there.

**Never stub silently.** Unimplemented paths raise `NotImplementedError` naming the spec section. A function returning a plausible wrong number is the worst failure mode in this domain — far worse than one that refuses to answer.

**Never fabricate data.** No synthetic prices, no placeholder fundamentals, no invented bond terms outside clearly-marked test fixtures. If a source is unavailable the field is null and the provenance says why.

**Never invent mnemonics or field names.** Spec §24 and §9.6 are the contract. Ask rather than coining.

**Ask when ambiguous.** Batch non-blocking questions; block on architectural ones.

**Push back when the spec is wrong.** If something is unsound, mathematically incorrect, or unachievable as written, say so with your reasoning. I would rather change the spec than build something broken. Deference is not useful here.

---

## Definition of done, for the whole build

The spec's Appendix worked example is the acceptance test for the entire system. When complete, I should execute all eleven steps against real data, and step 11 must hold literally:

> **every number in steps 3–8 reproduces from published code and open data.**

Build toward that.

---

## Now

1. Read `docs/treble-tracker-spec.md` in full.
2. Read `CLAUDE.md`.
3. Read `PROGRESS.md`.
4. Enter plan mode, produce the plan described above, and stop for my approval.
