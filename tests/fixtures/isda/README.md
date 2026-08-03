# ISDA CDS Standard Model — published test grids

Six currency grids, each a `_grid.csv` (test cases) and a `_curve.csv` (the
RFR discount curve inputs), derived from the ISDA CDS Standard Model's
published RFR test grids downloaded from <https://www.cdsmodel.com>:

| file stem | currency | index | trade date |
| --- | --- | --- | --- |
| `usd_sofr_20220622` | USD | SOFR | 2022-06-22 |
| `aud_aonia_20210430` | AUD | AONIA | 2021-04-30 |
| `chf_saron_20210430` | CHF | SARON | 2021-04-30 |
| `eur_ester_20210430` | EUR | €STR | 2021-04-30 |
| `gbp_sonia_20210430` | GBP | SONIA | 2021-04-30 |
| `jpy_tona_20210430` | JPY | TONA | 2021-04-30 |

Converted from the published `.xlsx` to CSV, and trimmed to the 2,388 cases
at the standard 40% recovery with a unique (maturity, coupon, quoted spread)
— the full grids are ~7,165 rows each. Nothing else was altered: the
expected upfronts are ISDA's own.

Two trade dates rather than one, deliberately. The 2021-04-30 grids are
traded 41 days into a coupon period; USD 2022-06-22 is traded 2 days after a
roll. A single trade date cannot tell a front-stub error from a curve-level
one, and `tests/analytics/credit/test_isda_grid.py` reads that contrast.

The grids disagree on one column: `days_accrued` is the full day count from
the previous roll in the USD grid (20 Jun → 22 Jun = 2) and one less than it
in the five 2021 grids (20 Mar → 30 Apr = 41, published as 40). That is
ISDA's own inconsistency between the two grid vintages, and it is untouched
here. It does not affect anything tested: the column compared is
`clean_upfront`, which excludes accrued by construction — the grids' own
`cash_settlement = clean_upfront − accrued` relation confirms it.

## Licence and attribution

Distributed under the **ISDA CDS Standard Model Public Licence Version 1.0**,
which permits reproduction, distribution and derivative works, and requires
that copyright notices be retained and that a derivative work for external
use state its basis.

> Copyright © ISDA and S&P Global. All rights reserved.
>
> This application is based on the ISDA CDS Standard Model.

Treble Tracker does **not** incorporate ISDA's source code. It uses the
published test grids as an external check on its own independent
implementation, which is the entire point: a model validated only against
itself is not validated.
