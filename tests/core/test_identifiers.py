"""Identifier validation and parsing (spec §9.2, §5.1)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from treble.core.identifiers import (
    Figi,
    Lei,
    YellowKey,
    figi_check_digit,
    parse_security,
    validate_figi,
    validate_lei,
)

# The spec's own example FIGI (§9.2).
VALID_FIGI = "BBG000BLNNH6"
# GLEIF's own LEI — published, stable, and check-digit-valid.
VALID_LEI = "506700GE1G29325QX363"


class TestFigi:
    def test_spec_example_is_valid(self) -> None:
        assert validate_figi(VALID_FIGI) == VALID_FIGI
        assert str(Figi(value=VALID_FIGI.lower())) == VALID_FIGI

    def test_check_digit_matches_spec_example(self) -> None:
        assert figi_check_digit(VALID_FIGI[:11]) == int(VALID_FIGI[11])

    @pytest.mark.parametrize(
        "bad",
        [
            "BBG000BLNNH7",  # wrong check digit
            "BAG000BLNNH6",  # vowel
            "BB0000BLNNH6",  # third char must be G
            "BBG000BLNNH",  # too short
            "",
        ],
    )
    def test_rejects(self, bad: str) -> None:
        with pytest.raises(ValueError):
            validate_figi(bad)

    @given(st.integers(min_value=0, max_value=11))
    def test_any_single_char_corruption_detected(self, position: int) -> None:
        corrupted = list(VALID_FIGI)
        original = corrupted[position]
        corrupted[position] = "9" if original != "9" else "8"
        candidate = "".join(corrupted)
        with pytest.raises(ValueError):
            validate_figi(candidate)


class TestLei:
    def test_gleif_own_lei_is_valid(self) -> None:
        assert validate_lei(VALID_LEI) == VALID_LEI
        assert str(Lei(value=VALID_LEI)) == VALID_LEI

    @pytest.mark.parametrize(
        "bad",
        [
            VALID_LEI[:-1] + ("0" if VALID_LEI[-1] != "0" else "1"),  # checksum broken
            VALID_LEI[:-1],  # too short
            VALID_LEI[:-2] + "AB",  # check digits must be numeric
            "",
        ],
    )
    def test_rejects(self, bad: str) -> None:
        with pytest.raises(ValueError):
            validate_lei(bad)


class TestParseSecurity:
    """Every security form appearing in spec §5.1 must parse."""

    def test_equity_with_venue(self) -> None:
        q = parse_security("IBM US Equity")
        assert (q.ticker, q.venue, q.key) == ("IBM", "US", YellowKey.EQUITY)
        assert q.display() == "IBM US Equity"

    def test_corp_bond_descriptor(self) -> None:
        q = parse_security("IBM 4.15 05/15/39 Corp")
        assert q.ticker == "IBM"
        assert q.key == YellowKey.CORP
        assert q.descriptor == "4.15 05/15/39"
        assert q.venue is None

    def test_mortgage_tba(self) -> None:
        q = parse_security("FNCL 5.5 Mtge")
        assert (q.ticker, q.descriptor, q.key) == ("FNCL", "5.5", YellowKey.MTGE)

    def test_currency(self) -> None:
        q = parse_security("EURUSD Crncy")
        assert (q.ticker, q.key) == ("EURUSD", YellowKey.CRNCY)

    def test_commodity_future(self) -> None:
        q = parse_security("CL1 Cmdty")
        assert (q.ticker, q.key) == ("CL1", YellowKey.CMDTY)

    def test_index(self) -> None:
        q = parse_security("SPX Index")
        assert (q.ticker, q.key) == ("SPX", YellowKey.INDEX)

    def test_macro_series_as_ticker(self) -> None:
        # §7.4: macro series are addressable as tickers.
        q = parse_security("USGG10YR Index")
        assert (q.ticker, q.key) == ("USGG10YR", YellowKey.INDEX)

    def test_govt_structured_ticker(self) -> None:
        # §9.2: structured OTC tickers, e.g. the current 10y Treasury.
        q = parse_security("CT10 Govt")
        assert (q.ticker, q.key) == ("CT10", YellowKey.GOVT)

    def test_yellow_key_aliases(self) -> None:
        assert YellowKey.parse("EQUITY") == YellowKey.EQUITY
        assert YellowKey.parse("M-MKT") == YellowKey.MMKT
        assert YellowKey.parse("Crncy") == YellowKey.CRNCY

    def test_rejects_no_key(self) -> None:
        with pytest.raises(ValueError):
            parse_security("IBM")
        with pytest.raises(ValueError):
            parse_security("IBM US Stonk")
