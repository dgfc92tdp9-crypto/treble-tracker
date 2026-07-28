"""The bond maths against the US Treasury's own auction results.

Treasury publishes both the price and the yield for every auction, computed
independently of anything here. Reproducing their yield from their price is
therefore an external check rather than a self-consistency one: no textbook
example, no hand-built lattice, just the sovereign issuer's own arithmetic.

Offline, from a recorded fixture (CLAUDE.md §7 — no network in tests).

Two conventions this pins down:

- **``dated_date``, not ``issue_date``, starts the accrual.** They differ on
  a reopening. Measured on this fixture the correct convention roughly
  thirds the residual (about 0.07 bp against 0.20 bp) — real, but small.
  Stated precisely because the first draft of this file claimed the issue
  date caused 10-30 bp errors, which was false: those came from an ad-hoc
  query that blended separate auctions of the same reopened CUSIP.
- **A TIPS cannot be priced as a nominal bond.** Treasury publishes
  inflation-indexed notes under the same "Note"/"Bond" security types, and
  only ``inflation_index_security`` separates them. Priced as nominal, a
  5-Year TIPS in this fixture returns a 1.32% real yield that would sit
  beside 4% nominal yields looking entirely plausible.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from treble.analytics.bonds.pricing import yield_from_price
from treble.analytics.bonds.spec import DayCount, FixedBondSpec, Frequency

FIXTURE = Path(__file__).parents[2] / "fixtures" / "treasury" / "auctions_coupon_securities.json"

#: Treasury rounds published prices to six decimals and yields to three or
#: four, so exact agreement is not available. Anything above this would mean
#: a convention error rather than rounding — the observed worst case across
#: the fixture is under a tenth of a basis point.
TOLERANCE_BP = 0.25


def _number(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _nominal_coupon_auctions() -> list[dict[str, object]]:
    records = json.loads(FIXTURE.read_text())["data"]
    return [
        r
        for r in records
        if r.get("security_type") in ("Note", "Bond")
        and r.get("inflation_index_security") != "Yes"
        and _number(r.get("int_rate"))
        and _number(r.get("price_per100")) is not None
        and _number(r.get("high_yield")) is not None
    ]


def _spec(record: dict[str, object]) -> FixedBondSpec:
    return FixedBondSpec(
        coupon=_number(record["int_rate"]) / 100.0,  # type: ignore[operator]
        # The accrual start, which differs from the issue date on a reopening.
        issue_date=date.fromisoformat(str(record["dated_date"])),
        maturity=date.fromisoformat(str(record["maturity_date"])),
        frequency=Frequency.SEMIANNUAL,
        day_count=DayCount.ACT_ACT_ICMA,  # Treasury notes and bonds
        settlement_days=0,  # auction price settles on the issue date
    )


AUCTIONS = _nominal_coupon_auctions()


def test_the_fixture_actually_contains_auctions() -> None:
    """Guards the checks below: an empty list would make every
    parametrised test vacuously pass."""
    assert len(AUCTIONS) >= 40


@pytest.mark.parametrize(
    "record", AUCTIONS, ids=[f"{r['cusip']}-{r['auction_date']}" for r in AUCTIONS]
)
def test_reproduces_the_published_yield(record: dict[str, object]) -> None:
    published = _number(record["high_yield"])
    assert published is not None
    computed = (
        yield_from_price(
            _spec(record),
            _number(record["price_per100"]),  # type: ignore[arg-type]
            as_of=date.fromisoformat(str(record["issue_date"])),
        ).value
        * 100.0
    )
    assert abs(computed - published) * 100 < TOLERANCE_BP


class TestConventionsThatWereWrongFirst:
    def test_reopenings_are_present_in_the_fixture(self) -> None:
        """Without a reopening in the data the dated-date convention would
        be untested — every bond would have dated == issue."""
        reopenings = [r for r in AUCTIONS if r.get("dated_date") != r.get("issue_date")]
        assert reopenings, "fixture has no reopening; the accrual-start rule is untested"

    def test_the_dated_date_is_measurably_the_better_convention(self) -> None:
        """The kill-test for the accrual-start rule.

        Asserts what is actually true — that `dated_date` fits Treasury's
        published yield more closely than `issue_date` on every reopening —
        rather than the stronger claim that `issue_date` breaks outright.
        Both stay inside the tolerance here; the point is that the choice is
        not arbitrary and cannot be silently reverted."""
        reopenings = [r for r in AUCTIONS if r.get("dated_date") != r.get("issue_date")]
        assert reopenings
        for reopening in reopenings:
            published = _number(reopening["high_yield"])
            assert published is not None
            correct = abs(
                yield_from_price(
                    _spec(reopening),
                    _number(reopening["price_per100"]),  # type: ignore[arg-type]
                    as_of=date.fromisoformat(str(reopening["issue_date"])),
                ).value
                * 100.0
                - published
            )
            assert correct <= self._error_using_issue_date(reopening, published)

    @staticmethod
    def _error_using_issue_date(reopening: dict[str, object], published: float) -> float:
        published_check = published
        assert published_check is not None
        wrong = FixedBondSpec(
            coupon=_number(reopening["int_rate"]) / 100.0,  # type: ignore[operator]
            issue_date=date.fromisoformat(str(reopening["issue_date"])),  # the mistake
            maturity=date.fromisoformat(str(reopening["maturity_date"])),
            frequency=Frequency.SEMIANNUAL,
            day_count=DayCount.ACT_ACT_ICMA,
            settlement_days=0,
        )
        computed = (
            yield_from_price(
                wrong,
                _number(reopening["price_per100"]),  # type: ignore[arg-type]
                as_of=date.fromisoformat(str(reopening["issue_date"])),
            ).value
            * 100.0
        )
        return abs(computed - published_check)

    def test_the_fixture_contains_inflation_indexed_securities(self) -> None:
        """They are excluded above, so their presence must be asserted or
        the exclusion silently stops protecting anything."""
        records = json.loads(FIXTURE.read_text())["data"]
        assert [r for r in records if r.get("inflation_index_security") == "Yes"]
