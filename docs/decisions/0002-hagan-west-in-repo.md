# 0002 - Hagan–West monotone convex interpolation implemented in-repo

2026-07-25 | Status: accepted

## Context

Spec §11.1 names monotone convex (Hagan–West) as the default curve interpolation, chosen
specifically because it cannot produce the oscillating or negative forwards that cubic splines
on zeros can. CLAUDE.md §2 fixes QuantLib as the analytics core — but QuantLib does not ship
the Hagan–West monotone convex method (its closest offerings are Hyman-filtered monotonic
cubic variants, which are not the same algorithm and do not carry the same forward-positivity
guarantee).

## Decision

Implement Hagan–West monotone convex ourselves in `treble/analytics/curves/`, following the
published paper (Hagan & West, "Interpolation Methods for Curve Construction", *Applied
Mathematical Finance*, 2006), and validate against the paper's worked example table as a
golden test. All other supported interpolations (linear zeros, log-linear discount factors,
natural cubic zeros, monotonic cubic) use QuantLib's implementations. The bootstrap asserts
the 1e-10 repricing property on every curve regardless of method.

## Consequences

- Easy: the spec's named default is honoured exactly, with a published reference to validate
  against; the interpolation choice remains a `CurveConfig` enum value (I4) so nothing
  downstream cares which library provided it.
- Hard: we own ~300 lines of numerical code and its edge cases (the paper's amelioration
  step, boundary handling) rather than delegating to QuantLib.
- Forecloses: nothing — if QuantLib later gains the method, swapping is a config-mapping
  change validated by the same golden tests.
