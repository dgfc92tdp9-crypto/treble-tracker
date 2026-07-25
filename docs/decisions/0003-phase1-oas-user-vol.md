# 0003 - Phase 1 OAS: Hull–White lattice with explicit user-supplied volatility

2026-07-25 | Status: accepted (Jack, 2026-07-25)

## Context

Phase 1's gate includes `YAS` with golden-value tests, and spec §10.2 defines OAS via a
short-rate model calibrated to the swaption volatility surface. But `VCUB` and its input
source (the DTCC SDR adapter) are Phase 2 scope (§23.3). Phase 1 therefore cannot calibrate
to a market surface without pulling Phase 2 forward.

## Decision

Build the full OAS engine in Phase 1 — Hull–White short-rate model, trinomial lattice with
time steps aligned to cash-flow and call dates, rollback with optimal exercise, spread solve —
with the volatility (and mean reversion) as **explicit user-supplied parameters**. Per I3, the
parameters are stamped in the model envelope, so every OAS is labelled as computed under a
stated vol rather than a calibrated one; the `MDL` entry documents this. At Phase 2, `VCUB`
calibration becomes the default parameter source; the engine does not change.

Golden tests: cross-check against a hand-built independent lattice for simple callables, plus
the Z-spread ≥ OAS (callable) property and option cost = Z − OAS identity.

## Consequences

- Easy: `YAS` is complete for callables in Phase 1; no rework at Phase 2 — calibration plugs
  in as a parameter provider.
- Hard: a Phase 1 OAS is only as good as the vol the user supplies; the envelope and screen
  must (and do) surface that assumption honestly rather than implying a market-calibrated
  number.
- Forecloses: nothing.
