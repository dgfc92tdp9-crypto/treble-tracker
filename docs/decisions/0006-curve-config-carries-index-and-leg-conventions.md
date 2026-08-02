# 0006 - CurveConfig carries the index tenor and the swap legs' conventions

2026-08-01 | Status: accepted

## Context

Phase 2's `SWPM` criterion requires multi-curve, CSA-aware discounting (spec §11.1, §12.1).
That means a *forecast* curve — one that projects a named index — built against a separate
discount curve. Two facts about such a curve were not expressible in `CurveConfig` as it stood:

1. **Which index it forecasts.** A 3M-index curve and a 6M-index curve built from the *same*
   instrument selection and the *same* quotes produce different forwards, because the floating
   leg pays at a different frequency. Under the old schema those two curves differed only in
   their `name` — a display string. That put a semantic distinction in a human-readable label
   and made I4 (content-addressed curve configuration) depend on someone remembering to name
   two curves differently.

2. **What its swap instruments actually are.** The Phase 1 bootstrap accrued every leg of every
   instrument in the curve's single `day_count`. A USD swap accrues 30/360 on the fixed leg
   against ACT/360 on the floating, and pays semiannual fixed against quarterly floating. With
   one convention for both legs the curve reprices its inputs *under its own definition of
   them* — but a real market swap priced against that curve comes out **3.4bp** away from the
   quote it was built from. The curve was internally consistent and externally wrong, which is
   the failure mode this project keeps finding in itself.

The pinned golden hash in `tests/analytics/curves/test_i4_config.py` says a change to the
serialisation "is a breaking change requiring a decision record, not a test update". This is
that record. The test worked: the schema change could not be made quietly.

## Decision

Add to `CurveConfig`:

- `index_tenor: str | None` — the index this curve forecasts, `None` for a discounting or
  overnight curve. Also exposes `index_frequency`, restricted to tenors a swap market quotes.
- `fixed_leg_day_count` / `float_leg_day_count` — the swap instruments' own leg conventions.
- `swap_fixed_frequency` — fixed payments per year on those instruments.

The three convention fields default to `None`/annual, which reproduces the Phase 1 single-curve
behaviour exactly, so existing curves are unchanged in *value*. Their **hashes change**, because
the serialised schema changed.

Also add `InstrumentKind.BASIS` (tenor basis swaps, spec §11.1). Adding an enum member does not
alter the hash of any config that does not use it.

## Consequences

**The pinned hash is re-pinned.** `af6a2d6d…` becomes `0f5b8cdc…`. Every I3 envelope stamped
before 2026-08-01 references a config hash that cannot be recomputed under the current schema.
Accepted because no such envelope has been persisted — the analytics registry stamps hashes into
results at computation time, and nothing in the store holds one. Had they been persisted, this
would have needed a schema version in the hash preimage instead.

**Curve identity now survives renaming.** Two curves that differ only in the index they project
hash differently, which is what I4 is for.

**Curves meant to reprice market swaps must say so.** A config that leaves the leg conventions
unset still builds, and still reprices its inputs under its own definition — but its par rates
will disagree with the market's by the ratio between the conventions (365/360 is 1.4%, which on
a 4% rate is about 5bp). The default is the compatible choice rather than the correct one, which
is a real sharp edge; it is documented on the fields and exercised by
`tests/analytics/derivatives/test_swap.py::TestTheCurveRepricesItsOwnInputs`, where a trade
written to match a curve input reprices to its quote to under 0.01bp only because the
conventions are stated on both.

**What this forecloses:** nothing yet. Cross-currency and inflation curves will need further
fields (FX spot reference, index lag, seasonality), and each will be another hash break unless
the preimage grows a version. That decision is deferred until the first of them is built,
because versioning the preimage now would be guessing at its shape.
