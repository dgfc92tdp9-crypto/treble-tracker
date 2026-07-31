# Phase 1 audit — read as an investor, not as the author

Written 2026-07-31, after Phase 1 was declared complete. Every figure below
was measured, not recalled.

The question this asks is not "did the work packages finish" — they did, and
CI is green on a clean checkout. It is the harder one: **would someone
funding this consider it the best model obtainable at zero cost?** On that
test Phase 1 has real gaps, and three of them are serious.

---

## What the store actually holds

| Namespace | Subjects | Facts |
|---|---|---|
| `cik:` (filers) | 5,746 | 2,007,886 |
| `fred:` (macro/index series) | 19 | 9,899 |
| `cusip:` (Treasuries) | 142 | 3,968 |

5,352 filers carry more than 100 facts each, so breadth is real. The gaps are
what is *absent*.

---

## Finding 1 — three adapters were built, tested, and never run

`gleif.py`, `openfigi.py` and `nport.py` have **zero entries in the ingest
log**. They have fixture tests, which is why WP6 passed; nothing checked
that they had ever processed a live payload.

The consequence is that the store has no `lei:` namespace and no FIGI
mapping. **WP7 is titled "security master + entity graph populated" and the
entity graph is not populated.** The work package passed on the machinery
existing.

This is the same shape as the two enforcement gaps the gate audit found:
the mechanism worked, and nothing checked it was switched on. It is now
three occurrences, which makes it the dominant failure mode of this project
rather than a coincidence.

**Fix:** run them, and add a check that every adapter the universe declares
has at least one ingest-log entry — so "built but never run" fails a gate
instead of passing one.

---

## Finding 2 — no equity prices, at all

`select count(*) from facts where subject like 'cik:%' and field='PX_LAST'`
returns **0**.

An equity research workstation with no equity prices is the single largest
credibility gap in the product. `GP` and `HP` serve the Index namespace and
say so, which is honest, but it means no price exists for any of the 5,746
filers whose fundamentals are loaded.

The cause is documented and defensible — the free sources either require
defeating a bot challenge (Stooq) or breach a terms of service (Yahoo), and
both were refused. But *defensible* is not the same as *solved*.

**Fix:** Tiingo's free tier gives consolidated, split- and dividend-adjusted
EOD with documented methodology, plus delisted securities. It needs one free
registration. Until that exists, this gap should be stated on the product's
own front page rather than only in a design note.

---

## Finding 3 — no corporate bonds

Zero non-Treasury CUSIPs. 142 Treasury securities is the entire fixed-income
universe.

`YAS`, `SRCH` and the whole bond analytics stack — yield, duration,
convexity, OAS on a Hull-White lattice — operate on government paper only.
The OAS lattice, which is the most sophisticated analytic in the codebase,
has **no callable bond to price**, because no corporate bond has been
ingested.

**Fix:** the N-PORT adapter already extracts CUSIP, ISIN, balance and USD
fair value per holding, and `holdings.implied_price` already turns those
into a price with cross-filer dispersion. Running it would populate
thousands of corporate bonds with valuations from primary filings. The
machinery is finished and unused.

---

## Finding 4 — macro reach is 19 series out of ~800,000

FRED carries roughly 800,000 series. Nineteen are loaded. The curve is
complete (11 CMT tenors) and the index set is reasonable, but there is no
inflation, no employment, no policy rate history, no credit spreads beyond
one index, and no international data.

**Fixed.** 36 series, chosen deliberately and grouped in the config by the
question each answers: policy (SOFR, DFF), inflation (CPI headline and core,
PCE headline and core, 10y breakeven, 5y5y forward), activity (unemployment,
payrolls, claims, real GDP, M2), credit (IG, BBB and HY spreads) and
external (dollar index, EURUSD, USDJPY) — on top of the CMT curve and the
index set.

Not exhaustive, and deliberately so: FRED carries ~800,000 series, and a
store that ingests everything answers no question better than one that
ingests the right things. Each block is annotated in `config/universe.yaml`
with what it is for, so the next addition has to justify itself.

---

## Finding 5 — eight of eleven analytics packages are empty

| Package | Modules |
|---|---|
| bonds | 3 |
| curves | 4 |
| holdings | 1 |
| credit, derivatives, equity, mortgage, risk, tval, vol | **0** |

Phase 1's scope was bonds and curves, so this is not a missed commitment.
But `equity/` being empty deserves attention: equities are the most-used
asset class, 5,746 filers are loaded, and there is not one ratio, growth
rate or per-share computation. The fundamentals are displayed as filed and
nothing is derived from them.

**Fix:** a small, well-tested equity analytics module — margins, returns,
leverage, per-share figures, growth — each with an I3 envelope. Every input
is already in the store.

---

## Finding 6 — one quarter of XBRL history

The bulk archive loaded is `2026q1` alone. Every filer beyond the original
ten has a single quarter of data, so no trend, no growth rate and no
year-on-year comparison is possible for 5,736 of them.

**Fix:** ingest more quarters. Each is one 85 MB download and about eight
minutes. Twelve quarters would give three years of history across the whole
universe for under an hour of ingest.

---

## Finding 7 — accuracy validation is uneven

Genuinely strong:

- Bond yields reproduce the US Treasury's own auction yields to **0.07 bp**
  worst case across 46 auctions.
- Curve bootstrapping reprices every input instrument to **1e-10**, asserted
  as a property on every curve.
- Bulk XBRL reconciles with the companyfacts API on value *and* period.

Not validated at all:

- The Hull-White OAS lattice has no external reference — only internal
  consistency and a hand-built cross-check.
- `holdings.implied_price` has no case where its output is compared against
  a known market price.
- No end-to-end test asserts that a *screen* shows the same number the
  analytics produce; conformance pins layout, and the analytics tests pin
  values, but nothing joins them.

**Fix:** the third is the cheapest and highest value — a test that resolves
a screen and asserts a named cell equals an independently computed figure.

---

## Finding 8 — mutation testing still does not run

Recorded as a known defect since 2026-07-27 and still true. The suite's
strength is therefore unmeasured: 88% line coverage says the lines execute,
not that a test would notice if they were wrong.

---

## Finding 9 — consensus must group by report date (found while fixing Finding 2)

Widening N-PORT coverage took the store to **1,681 securities priced from
primary filings**, 341 of them held by more than one filer and therefore
cross-checkable. That is the mechanism working.

But the filings ingested carry **three different report dates**
(2026-03-31, 2026-04-30, 2026-05-31), and marks were combined across them.
The resulting dispersion — 113 bp on Exxon, 447 bp on Equinix — measures
*time drift plus valuation disagreement*, not valuation disagreement, and
nothing in the output says so.

The `holdings.consensus_price` model is not at fault; it combines the marks
it is given. The caller must group by report date first. Until it does, the
dispersion figure overstates filer disagreement and must not be read as a
confidence measure.

**Fixed.** `ImpliedMark` now carries its report date and `consensus_price`
refuses marks that span dates, so blending is impossible by construction
rather than a rule a caller must remember.

**And the fix validated the method.** Grouped correctly, dispersion across
three independent fund families collapses to **0.0 bp** — Exxon at 169.66,
Phillips 66 at 182.18, Targa at 250.73, each priced identically by three
filers who never saw each other's books. The earlier 113-447 bp spreads
were entirely elapsed time.

That is a stronger result than the fix was aiming for. It means
`valUSD / balance` does not approximate the market price, it recovers it:
three-way independent agreement to the cent, from primary filings, at zero
cost. The dispersion figure is now a real confidence signal, and where it
reads zero that is evidence rather than an absence of data.

## Priority

1. **Run the three unrun adapters** and gate on it — closes Finding 1, and
   Finding 3 follows from N-PORT.
2. **Equity analytics module** — Finding 5, uses data already loaded.
3. **More XBRL quarters** — Finding 6, unlocks every growth measure.
4. **Broader FRED coverage** — Finding 4, keyless.
5. **Screen-to-analytics end-to-end test** — Finding 7.
6. **Equity prices** — Finding 2, largely addressed via N-PORT (1,681
   securities); a licensed daily feed still needs one free registration.
7. **Group consensus by report date** — Finding 9, and the most urgent of
   these because it is a number currently presented as more meaningful than
   it is.
