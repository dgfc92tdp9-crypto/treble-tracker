"""ISIN parsing, validation, and the CUSIP bridge.

Found by a scan that ran every screen binding against the live store and
asked which ones refused. `sys:entity_owners` reported "carries no LEI" for
every equity, which was true; chasing it showed something larger. Bonds
resolved only by CUSIP, the store held 1,861 ISINs against 147 CUSIPs, and
N-PORT — the source of nearly all of them — publishes ISINs. So 93% of the
bond universe was addressable only by an identifier no source in the system
writes, and the 373,125-fact GLEIF relationship graph behind it could not be
reached from any screen at all.

The check digit is the part worth testing hard: it is the difference
between rejecting a typo and resolving it to a subject with no facts, which
renders as "no data for this bond" rather than "you mistyped it".
"""

from __future__ import annotations

import pytest

from treble.core.identifiers import (
    cusip_from_isin,
    isin_check_digit,
    isin_from_cusip,
    looks_like_isin,
)

#: Published ISINs, verifiable outside this repository. Self-consistency
#: between `isin_from_cusip` and `looks_like_isin` would be satisfied by two
#: functions sharing one wrong algorithm, so the anchors are external.
PUBLISHED = {
    "US0378331005": "037833100",  # Apple Inc.
    "US4592001014": "459200101",  # IBM
    "US49177J1025": "49177J102",  # from the live store
    "GB0002634946": None,  # BAE Systems — a UK ISIN carries no CUSIP
}


class TestTheCheckDigit:
    @pytest.mark.parametrize("isin", sorted(PUBLISHED))
    def test_published_isins_validate(self, isin: str) -> None:
        assert looks_like_isin(isin)

    @pytest.mark.parametrize("isin", sorted(PUBLISHED))
    def test_a_corrupted_check_digit_is_rejected(self, isin: str) -> None:
        """The whole point. A twelve-character typo is otherwise
        indistinguishable from an ISIN and resolves to an empty subject."""
        wrong = isin[:11] + str((int(isin[11]) + 1) % 10)
        assert not looks_like_isin(wrong)

    def test_letters_expand_before_the_luhn_pass_not_after(self) -> None:
        """Expanding after would change which digits fall in the doubled
        positions, and be wrong for exactly the ISINs containing letters —
        which is most of them. 49177J102 contains a J, so this case would
        fail under the wrong ordering while a digits-only ISIN would not."""
        assert isin_check_digit("US49177J102") == "5"

    @pytest.mark.parametrize(
        "bad",
        ["", "US037833100", "US03783310055", "0S0378331005", "US03783 31005"],
    )
    def test_malformed_strings_are_not_isins(self, bad: str) -> None:
        assert not looks_like_isin(bad)


class TestTheCusipBridge:
    @pytest.mark.parametrize(("isin", "cusip"), sorted(PUBLISHED.items()))
    def test_the_embedded_cusip_is_extracted_only_where_there_is_one(
        self, isin: str, cusip: str | None
    ) -> None:
        """Outside US and CA the nine characters after the country code are
        a national number that is not a CUSIP. Returning one anyway would
        produce an identifier that looks right and refers to nothing."""
        assert cusip_from_isin(isin) == cusip

    @pytest.mark.parametrize(("isin", "cusip"), sorted(PUBLISHED.items()))
    def test_the_round_trip_reproduces_the_published_isin(
        self, isin: str, cusip: str | None
    ) -> None:
        if cusip is None:
            pytest.skip("no CUSIP inside a non-North-American ISIN")
        assert isin_from_cusip(cusip) == isin

    def test_canada_is_supported_and_differs_from_the_us(self) -> None:
        """A CUSIP alone does not say which country prefix it takes, so
        both are tried at resolution. They must not produce the same
        string, or trying both would be pointless."""
        assert isin_from_cusip("037833100", country="CA") != isin_from_cusip("037833100")
        assert looks_like_isin(isin_from_cusip("037833100", country="CA"))
