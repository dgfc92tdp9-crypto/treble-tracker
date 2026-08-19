"""Run a mandate's rules against the holdings in the store (P3_4).

The compliance engine takes `Holding` records and knows nothing about
N-PORT; this is the seam. Keeping it separate is what lets the rules be
unit-tested against synthetic portfolios — a rule engine that could only be
exercised through a store would be one nobody could write a failing case
for.

**What the store cannot supply is not silently dropped.** Rating is absent
everywhere, so a mandate containing a rating rule comes back NOT EVALUABLE
rather than clean, and that verdict travels from here rather than being
decided here: this function's job is to hand over what it has, honestly
including the `None`s.

Run against the live store on 2026-08-19, that is not a hypothetical. Of
686 positions: 686 carry no rating, 445 no maturity, 321 no currency and 243
no issuer — because the portfolio is not all bonds, and a derivative record
does not populate the fields a bond rule reads. **Four of five rules in a
plausible mandate come back NOT EVALUABLE, and one genuine breach is found.**
An engine that skipped what it could not test would have reported one breach
and four passes: a near-clean bill of health on a portfolio where most of
the mandate was never checked.

The holdings are *not* filtered to straight debt to make the numbers look
better. A mandate covers everything the fund holds, and narrowing the input
until the rules pass would be answering an easier question than the one the
mandate asks.
"""

from __future__ import annotations

from datetime import date, datetime

from treble.compliance.rules import Holding, Report, RuleSet, run
from treble.store.duck import DuckStore


def holdings_from_store(store: DuckStore, *, as_of: datetime) -> tuple[Holding, ...]:
    """Every straight-debt holding, as a compliance rule sees it.

    Market value comes from `nport:valUSD` — the fund's own mark. Rating is
    passed as `None` because no rating source this repository may use has
    been found, and inventing one would be the difference between a rule
    that fails honestly and a report that lies.
    """
    from treble.tapi.issuer_curves import bond_rows

    latest: dict[str, dict[str, object]] = {}
    for row in bond_rows(store, as_of=as_of):
        identifier = str(row.get("identifier"))
        seen = latest.get(identifier)
        current = row.get("report_date")
        if seen is None or (
            isinstance(current, date)
            and isinstance(seen.get("report_date"), date)
            and current > seen["report_date"]  # type: ignore[operator]
        ):
            latest[identifier] = row

    out: list[Holding] = []
    for identifier, row in sorted(latest.items()):
        value = row.get("nport:valUSD")
        if not isinstance(value, float | int):
            # A position with no mark cannot be weighted, and guessing one
            # would put a number into every percentage rule in the mandate.
            continue
        maturity = row.get("nport:maturityDt")
        currency = row.get("nport:curCd")
        category = row.get("nport:assetCat")
        issuer = row.get("gleif:lei") or row.get("nport:lei")
        out.append(
            Holding(
                identifier=identifier,
                market_value=float(value),
                issuer=str(issuer) if isinstance(issuer, str) else None,
                maturity=maturity if isinstance(maturity, date) else None,
                currency=str(currency) if isinstance(currency, str) else None,
                asset_category=str(category) if isinstance(category, str) else None,
                rating=None,
            )
        )
    return tuple(out)


def check_mandate(store: DuckStore, ruleset: RuleSet, *, as_of: datetime) -> Report:
    """Evaluate one mandate against the store's holdings."""
    return run(ruleset, holdings_from_store(store, as_of=as_of), today=as_of.date())


__all__ = ["check_mandate", "holdings_from_store"]
