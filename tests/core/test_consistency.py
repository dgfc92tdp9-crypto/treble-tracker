"""Accounting identities: the numbers on a screen must agree with each other.

Spec §14.1 requires it: *"a statement that does not foot is rejected
automatically."*

`period_from` and `check_screen_periods.py` answer "did this number come
from the period the heading claims". These answer the question neither can:
**do the numbers add up**, which catches a wrong value even when every
period lines up.

The identities here were chosen by running them over the live store, not by
intuition. `TestTheDiscardedIdentities` records the two that were thrown
out and why, because the obvious form of one of them is wrong about one
filing in six.
"""

from __future__ import annotations

import pytest

from treble.core.consistency import (
    ASSETS,
    ASSETS_CURRENT,
    BALANCE_TOTAL,
    EPS_BASIC,
    IDENTITIES,
    INCOME_TO_COMMON,
    LIABILITIES,
    LIABILITIES_CURRENT,
    SHARES_BASIC,
    check,
)

#: Apple's Q2 FY2026, exactly as the store holds it.
APPLE = {
    ASSETS: 371_082_000_000.0,
    BALANCE_TOTAL: 371_082_000_000.0,
    LIABILITIES: 264_591_000_000.0,
    EPS_BASIC: 2.02,
    SHARES_BASIC: 14_673_278_000.0,
    INCOME_TO_COMMON: 29_578_000_000.0,
}


class TestAGoodStatement:
    def test_apple_passes(self) -> None:
        """Real filed figures. If this ever fails, the identities are wrong,
        not Apple."""
        assert check(APPLE) == ()

    def test_a_statement_with_nothing_in_it_passes(self) -> None:
        """Most filers tag a fraction of the taxonomy. Treating a missing
        tag as a broken statement would flag almost every company, and a
        check that fires on everything is one nobody reads."""
        assert check({}) == ()

    def test_a_half_tagged_statement_passes(self) -> None:
        assert check({ASSETS: 100.0}) == ()


class TestTheBalanceSheetMustFoot:
    def test_a_total_that_disagrees_with_assets_is_caught(self) -> None:
        broken = {**APPLE, BALANCE_TOTAL: 300_000_000_000.0}
        (violation,) = check(broken)
        assert violation.identity == "balance sheet foots"
        assert violation.relative > 0.2

    def test_a_rounding_difference_is_not(self) -> None:
        """Filers round to thousands. A check that fired on that would fire
        constantly."""
        assert check({**APPLE, BALANCE_TOTAL: 371_082_000_000.0 - 1_000_000.0}) == ()

    def test_current_assets_above_total_assets_is_caught(self) -> None:
        (violation,) = check({**APPLE, ASSETS_CURRENT: 400_000_000_000.0})
        assert violation.identity == "current assets within total assets"

    def test_current_assets_below_total_is_fine(self) -> None:
        """A subtotal is *allowed* to be smaller — that is what makes it a
        subtotal. An equality check here would flag every healthy filer."""
        assert check({**APPLE, ASSETS_CURRENT: 150_000_000_000.0}) == ()

    def test_current_liabilities_above_total_is_caught(self) -> None:
        (violation,) = check({**APPLE, LIABILITIES_CURRENT: 300_000_000_000.0})
        assert violation.identity == "current liabilities within total liabilities"


class TestEarningsPerShareReconciles:
    def test_a_mismatched_income_is_caught(self) -> None:
        """EPS x shares is an independent statement of income. When the two
        disagree, one of them came from somewhere else."""
        (violation,) = check({**APPLE, INCOME_TO_COMMON: 10_000_000_000.0})
        assert violation.identity == "earnings per share reconciles"

    def test_the_real_figures_reconcile(self) -> None:
        """2.02 x 14,673,278,000 = 29.64bn against a reported 29.578bn —
        0.2% apart, which is the rounding in a two-decimal EPS."""
        assert check(APPLE) == ()

    def test_a_stale_eps_from_another_period_is_caught(self) -> None:
        """The defect this whole file exists for, in its per-share form: a
        figure that is internally plausible and belongs to another quarter."""
        (violation,) = check({**APPLE, EPS_BASIC: 1.26})
        assert violation.identity == "earnings per share reconciles"


class TestTheAppleDefect:
    """The failure that prompted all of this, checked end to end.

    `DES` and `FA` showed Apple's Q4 FY2018 revenue — 62,900,000,000 — under
    a "3 months to 2026-03-28" heading beside that quarter's real net income.
    A 47% net margin for a company that runs about 26%.
    """

    def test_the_2018_revenue_beside_2026_earnings_does_not_reconcile(self) -> None:
        """Revenue is not in any identity, so this is caught through the
        per-share relationship: the 2018 statement's EPS and share count
        cannot produce 2026's income."""
        mixed = {
            **APPLE,
            EPS_BASIC: 2.91,  # Q4 FY2018 as filed
            SHARES_BASIC: 4_754_986_000.0,  # pre-split share count, FY2018
        }
        violations = check(mixed)
        assert violations, "a statement assembled from two eras reconciled anyway"
        assert violations[0].identity == "earnings per share reconciles"

    def test_a_coherent_2018_statement_passes(self) -> None:
        """Proves the assertion above turns on the *mixing* rather than on
        the 2018 numbers being unusual. Taken together they are a perfectly
        good statement — they simply are not this quarter's."""
        coherent = {
            EPS_BASIC: 2.91,
            SHARES_BASIC: 4_754_986_000.0,
            INCOME_TO_COMMON: 4_754_986_000.0 * 2.91,
        }
        assert check(coherent) == ()


class TestTheIdentitiesWereMeasured:
    """Each identity records how often it broke on the live store.

    Not decoration: it is the reason the identity is here rather than in the
    list of discarded ones, and it is what a later reader needs to argue
    with the choice.
    """

    @pytest.mark.parametrize("identity", IDENTITIES, ids=lambda i: i.name)
    def test_every_identity_holds_almost_always(self, identity: object) -> None:
        assert identity.measured_break_rate < 0.02, (  # type: ignore[attr-defined]
            "an identity that fails often is noise, and noise teaches a reader to ignore the column"
        )

    @pytest.mark.parametrize("identity", IDENTITIES, ids=lambda i: i.name)
    def test_every_identity_was_measured_on_a_real_sample(self, identity: object) -> None:
        assert identity.sample > 1_000  # type: ignore[attr-defined]


class TestTheDiscardedIdentities:
    """Why the obvious checks are absent, pinned so nobody re-adds them.

    `EPS x shares vs NetIncomeLoss` breaks 16.19% of the time, because
    earnings per share is computed on income *available to common
    shareholders* — different wherever preferred dividends or
    non-controlling interests exist. The correct form breaks 1.18%.

    `Assets = Liabilities + StockholdersEquity` breaks 11.18%, because
    filers restate one leg without the others and the source stops footing.
    """

    def test_net_income_is_not_what_eps_is_checked_against(self) -> None:
        from treble.core import consistency

        assert not hasattr(consistency, "NET_INCOME")
        assert "AvailableToCommon" in consistency.INCOME_TO_COMMON

    def test_liabilities_plus_equity_is_not_an_identity(self) -> None:
        """The filer's own `LiabilitiesAndStockholdersEquity` total is used
        instead — 0.05% break against 11.18%."""
        names = {i.name for i in IDENTITIES}
        assert "balance sheet foots" in names
        for identity in IDENTITIES:
            assert "StockholdersEquity:USD" not in identity.right_of
