# ISDA CDS Standard Model — published test grids

`usd_sofr_20220622_grid.csv` and `usd_sofr_20220622_curve.csv` are derived
from the ISDA CDS Standard Model's published RFR test grid for USD/SOFR,
trade date 2022-06-22, downloaded from <https://www.cdsmodel.com>.

Converted from the published `.xlsx` to CSV, and trimmed to the 2,388 cases
at the standard 40% recovery with a unique (maturity, coupon, quoted spread)
— the full grid is 7,165 rows. Nothing else was altered: the expected
upfronts are ISDA's own.

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
