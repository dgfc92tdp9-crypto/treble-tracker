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

Implement Hagan–West monotone convex ourselves in `treble/analytics/curves/hagan_west.py`,
following the published paper (Hagan & West, "Interpolation Methods for Curve Construction",
*Applied Mathematical Finance*, 2006).

**Amendment (same date):** the bootstrap solver is generic over an in-repo `Interpolator`
protocol, because QuantLib's bootstrap cannot take a custom Python interpolation trait — so
the other methods (linear zeros, log-linear discount, natural/monotonic cubic zeros) are also
provided as thin in-repo interpolators (NumPy/SciPy, both in the fixed stack), with a single
global solve (`scipy.optimize.root`) driving node zeros to reprice every input to 1e-10.
QuantLib remains the analytics core for schedules, calendars, day counts and all bond/OAS
math; QuantLib curve construction is used as an independent *cross-check* in golden tests
rather than as the bootstrap implementation.

Validation: closed-form g-integrals cross-checked against quadrature under Hypothesis;
structural properties (node repricing, forward positivity, forward continuity, forward-integral
consistency) asserted; repricing property asserted for every interpolation method on every
build — the constructor refuses to return a curve that misses 1e-10.

## Consequences

- Easy: the spec's named default is honoured exactly, with a published reference to validate
  against; the interpolation choice remains a `CurveConfig` enum value (I4) so nothing
  downstream cares which library provided it.
- Hard: we own ~300 lines of numerical code and its edge cases (the paper's amelioration
  step, boundary handling) rather than delegating to QuantLib.
- Forecloses: nothing — if QuantLib later gains the method, swapping is a config-mapping
  change validated by the same golden tests.
