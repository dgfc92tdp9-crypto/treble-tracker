"""Instrument and entity identifiers (spec §9.2).

FIGI is the primary instrument key, LEI the primary entity key, TUID the
internal surrogate. The human-facing composite is ticker + exchange + yellow
key ("IBM US Equity"). Yellow keys select the asset-class namespace (§3.1):
the same ticker resolves to different objects under different keys.
"""

from __future__ import annotations

import enum
import re
import uuid
from typing import NewType

from pydantic import BaseModel, ConfigDict, field_validator

TUID = NewType("TUID", str)
"""Treble Unique Identifier — internal surrogate key, stable across all reference changes."""


from mutmut.mutation.trampoline import wrap_in_trampoline as _mutmut_mutated, MutantDict
mutants_x_new_tuid__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_new_tuid__mutmut)
def new_tuid() -> TUID:
    return TUID(uuid.uuid4().hex)


def x_new_tuid__mutmut_orig() -> TUID:
    return TUID(uuid.uuid4().hex)


def x_new_tuid__mutmut_1() -> TUID:
    return TUID(None)

mutants_x_new_tuid__mutmut['_mutmut_orig'] = x_new_tuid__mutmut_orig # type: ignore # mutmut generated
mutants_x_new_tuid__mutmut['x_new_tuid__mutmut_1'] = x_new_tuid__mutmut_1 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut: MutantDict = {}  # type: ignore


class YellowKey(enum.Enum):
    """Asset-class namespace keys (spec §3.1). Values are the display forms."""

    GOVT = "Govt"
    CORP = "Corp"
    MTGE = "Mtge"
    MUNI = "Muni"
    MMKT = "M-Mkt"
    PFD = "Pfd"
    EQUITY = "Equity"
    CMDTY = "Cmdty"
    INDEX = "Index"
    CRNCY = "Crncy"
    CRYPTO = "Crypto"
    CLIENT = "Client"

    @classmethod
    @_mutmut_mutated(mutants_xǁYellowKeyǁparse__mutmut, is_classmethod = True)
    def parse(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", "")
        for key in cls:
            if normalised in (key.name.replace("_", ""), key.value.upper().replace("-", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_orig(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", "")
        for key in cls:
            if normalised in (key.name.replace("_", ""), key.value.upper().replace("-", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_1(cls, text: str) -> YellowKey:
        normalised = None
        for key in cls:
            if normalised in (key.name.replace("_", ""), key.value.upper().replace("-", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_2(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace(None, "")
        for key in cls:
            if normalised in (key.name.replace("_", ""), key.value.upper().replace("-", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_3(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", None)
        for key in cls:
            if normalised in (key.name.replace("_", ""), key.value.upper().replace("-", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_4(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("")
        for key in cls:
            if normalised in (key.name.replace("_", ""), key.value.upper().replace("-", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_5(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", )
        for key in cls:
            if normalised in (key.name.replace("_", ""), key.value.upper().replace("-", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_6(cls, text: str) -> YellowKey:
        normalised = text.strip().lower().replace("-", "")
        for key in cls:
            if normalised in (key.name.replace("_", ""), key.value.upper().replace("-", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_7(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("XX-XX", "")
        for key in cls:
            if normalised in (key.name.replace("_", ""), key.value.upper().replace("-", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_8(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", "XXXX")
        for key in cls:
            if normalised in (key.name.replace("_", ""), key.value.upper().replace("-", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_9(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", "")
        for key in cls:
            if normalised not in (key.name.replace("_", ""), key.value.upper().replace("-", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_10(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", "")
        for key in cls:
            if normalised in (key.name.replace(None, ""), key.value.upper().replace("-", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_11(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", "")
        for key in cls:
            if normalised in (key.name.replace("_", None), key.value.upper().replace("-", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_12(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", "")
        for key in cls:
            if normalised in (key.name.replace(""), key.value.upper().replace("-", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_13(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", "")
        for key in cls:
            if normalised in (key.name.replace("_", ), key.value.upper().replace("-", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_14(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", "")
        for key in cls:
            if normalised in (key.name.replace("XX_XX", ""), key.value.upper().replace("-", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_15(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", "")
        for key in cls:
            if normalised in (key.name.replace("_", "XXXX"), key.value.upper().replace("-", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_16(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", "")
        for key in cls:
            if normalised in (key.name.replace("_", ""), key.value.upper().replace(None, "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_17(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", "")
        for key in cls:
            if normalised in (key.name.replace("_", ""), key.value.upper().replace("-", None)):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_18(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", "")
        for key in cls:
            if normalised in (key.name.replace("_", ""), key.value.upper().replace("")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_19(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", "")
        for key in cls:
            if normalised in (key.name.replace("_", ""), key.value.upper().replace("-", )):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_20(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", "")
        for key in cls:
            if normalised in (key.name.replace("_", ""), key.value.lower().replace("-", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_21(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", "")
        for key in cls:
            if normalised in (key.name.replace("_", ""), key.value.upper().replace("XX-XX", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_22(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", "")
        for key in cls:
            if normalised in (key.name.replace("_", ""), key.value.upper().replace("-", "XXXX")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")

    @classmethod
    def xǁYellowKeyǁparse__mutmut_23(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", "")
        for key in cls:
            if normalised in (key.name.replace("_", ""), key.value.upper().replace("-", "")):
                return key
        raise ValueError(None)

mutants_xǁYellowKeyǁparse__mutmut['_mutmut_orig'] = YellowKey.xǁYellowKeyǁparse__mutmut_orig # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_1'] = YellowKey.xǁYellowKeyǁparse__mutmut_1 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_2'] = YellowKey.xǁYellowKeyǁparse__mutmut_2 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_3'] = YellowKey.xǁYellowKeyǁparse__mutmut_3 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_4'] = YellowKey.xǁYellowKeyǁparse__mutmut_4 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_5'] = YellowKey.xǁYellowKeyǁparse__mutmut_5 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_6'] = YellowKey.xǁYellowKeyǁparse__mutmut_6 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_7'] = YellowKey.xǁYellowKeyǁparse__mutmut_7 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_8'] = YellowKey.xǁYellowKeyǁparse__mutmut_8 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_9'] = YellowKey.xǁYellowKeyǁparse__mutmut_9 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_10'] = YellowKey.xǁYellowKeyǁparse__mutmut_10 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_11'] = YellowKey.xǁYellowKeyǁparse__mutmut_11 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_12'] = YellowKey.xǁYellowKeyǁparse__mutmut_12 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_13'] = YellowKey.xǁYellowKeyǁparse__mutmut_13 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_14'] = YellowKey.xǁYellowKeyǁparse__mutmut_14 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_15'] = YellowKey.xǁYellowKeyǁparse__mutmut_15 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_16'] = YellowKey.xǁYellowKeyǁparse__mutmut_16 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_17'] = YellowKey.xǁYellowKeyǁparse__mutmut_17 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_18'] = YellowKey.xǁYellowKeyǁparse__mutmut_18 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_19'] = YellowKey.xǁYellowKeyǁparse__mutmut_19 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_20'] = YellowKey.xǁYellowKeyǁparse__mutmut_20 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_21'] = YellowKey.xǁYellowKeyǁparse__mutmut_21 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_22'] = YellowKey.xǁYellowKeyǁparse__mutmut_22 # type: ignore # mutmut generated
mutants_xǁYellowKeyǁparse__mutmut['xǁYellowKeyǁparse__mutmut_23'] = YellowKey.xǁYellowKeyǁparse__mutmut_23 # type: ignore # mutmut generated


# FIGI: 12 chars — two consonants, 'G', eight consonant/digit, one check digit
# (ANSI X9.145). Vowels are excluded throughout.
_FIGI_RE = re.compile(r"^[B-DF-HJ-NP-TV-Z0-9]{2}G[B-DF-HJ-NP-TV-Z0-9]{8}[0-9]$")
# LEI: ISO 17442 — 18 alphanumeric + 2 check digits, validated mod 97-10.
_LEI_RE = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")
mutants_x__char_value__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x__char_value__mutmut)
def _char_value(ch: str) -> int:
    """0-9 -> 0-9, A-Z -> 10-35 (shared by the FIGI and LEI check algorithms)."""
    if ch.isdigit():
        return int(ch)
    return ord(ch) - ord("A") + 10


def x__char_value__mutmut_orig(ch: str) -> int:
    """0-9 -> 0-9, A-Z -> 10-35 (shared by the FIGI and LEI check algorithms)."""
    if ch.isdigit():
        return int(ch)
    return ord(ch) - ord("A") + 10


def x__char_value__mutmut_1(ch: str) -> int:
    """0-9 -> 0-9, A-Z -> 10-35 (shared by the FIGI and LEI check algorithms)."""
    if ch.isdigit():
        return int(None)
    return ord(ch) - ord("A") + 10


def x__char_value__mutmut_2(ch: str) -> int:
    """0-9 -> 0-9, A-Z -> 10-35 (shared by the FIGI and LEI check algorithms)."""
    if ch.isdigit():
        return int(ch)
    return ord(ch) - ord("A") - 10


def x__char_value__mutmut_3(ch: str) -> int:
    """0-9 -> 0-9, A-Z -> 10-35 (shared by the FIGI and LEI check algorithms)."""
    if ch.isdigit():
        return int(ch)
    return ord(ch) + ord("A") + 10


def x__char_value__mutmut_4(ch: str) -> int:
    """0-9 -> 0-9, A-Z -> 10-35 (shared by the FIGI and LEI check algorithms)."""
    if ch.isdigit():
        return int(ch)
    return ord(None) - ord("A") + 10


def x__char_value__mutmut_5(ch: str) -> int:
    """0-9 -> 0-9, A-Z -> 10-35 (shared by the FIGI and LEI check algorithms)."""
    if ch.isdigit():
        return int(ch)
    return ord(ch) - ord(None) + 10


def x__char_value__mutmut_6(ch: str) -> int:
    """0-9 -> 0-9, A-Z -> 10-35 (shared by the FIGI and LEI check algorithms)."""
    if ch.isdigit():
        return int(ch)
    return ord(ch) - ord("XXAXX") + 10


def x__char_value__mutmut_7(ch: str) -> int:
    """0-9 -> 0-9, A-Z -> 10-35 (shared by the FIGI and LEI check algorithms)."""
    if ch.isdigit():
        return int(ch)
    return ord(ch) - ord("a") + 10


def x__char_value__mutmut_8(ch: str) -> int:
    """0-9 -> 0-9, A-Z -> 10-35 (shared by the FIGI and LEI check algorithms)."""
    if ch.isdigit():
        return int(ch)
    return ord(ch) - ord("A") + 11

mutants_x__char_value__mutmut['_mutmut_orig'] = x__char_value__mutmut_orig # type: ignore # mutmut generated
mutants_x__char_value__mutmut['x__char_value__mutmut_1'] = x__char_value__mutmut_1 # type: ignore # mutmut generated
mutants_x__char_value__mutmut['x__char_value__mutmut_2'] = x__char_value__mutmut_2 # type: ignore # mutmut generated
mutants_x__char_value__mutmut['x__char_value__mutmut_3'] = x__char_value__mutmut_3 # type: ignore # mutmut generated
mutants_x__char_value__mutmut['x__char_value__mutmut_4'] = x__char_value__mutmut_4 # type: ignore # mutmut generated
mutants_x__char_value__mutmut['x__char_value__mutmut_5'] = x__char_value__mutmut_5 # type: ignore # mutmut generated
mutants_x__char_value__mutmut['x__char_value__mutmut_6'] = x__char_value__mutmut_6 # type: ignore # mutmut generated
mutants_x__char_value__mutmut['x__char_value__mutmut_7'] = x__char_value__mutmut_7 # type: ignore # mutmut generated
mutants_x__char_value__mutmut['x__char_value__mutmut_8'] = x__char_value__mutmut_8 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_figi_check_digit__mutmut)
def figi_check_digit(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_orig(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_1(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = None
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_2(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 1
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_3(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(None, start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_4(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=None):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_5(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_6(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, ):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_7(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=2):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_8(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = None
        if position % 2 == 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_9(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(None)
        if position % 2 == 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_10(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position / 2 == 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_11(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 3 == 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_12(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 != 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_13(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 == 1:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_14(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value = 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_15(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value /= 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_16(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 3
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_17(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total = sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_18(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total -= sum(int(d) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_19(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total += sum(None)
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_20(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total += sum(int(None) for d in str(value))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_21(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total += sum(int(d) for d in str(None))
    return (10 - total % 10) % 10


def x_figi_check_digit__mutmut_22(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) / 10


def x_figi_check_digit__mutmut_23(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 + total % 10) % 10


def x_figi_check_digit__mutmut_24(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (11 - total % 10) % 10


def x_figi_check_digit__mutmut_25(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 - total / 10) % 10


def x_figi_check_digit__mutmut_26(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 11) % 10


def x_figi_check_digit__mutmut_27(first_eleven: str) -> int:
    """FIGI check digit: double the value at every even position (1-indexed),
    sum the decimal digits of all values, take (10 - sum mod 10) mod 10."""
    total = 0
    for position, ch in enumerate(first_eleven, start=1):
        value = _char_value(ch)
        if position % 2 == 0:
            value *= 2
        total += sum(int(d) for d in str(value))
    return (10 - total % 10) % 11

mutants_x_figi_check_digit__mutmut['_mutmut_orig'] = x_figi_check_digit__mutmut_orig # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_1'] = x_figi_check_digit__mutmut_1 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_2'] = x_figi_check_digit__mutmut_2 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_3'] = x_figi_check_digit__mutmut_3 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_4'] = x_figi_check_digit__mutmut_4 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_5'] = x_figi_check_digit__mutmut_5 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_6'] = x_figi_check_digit__mutmut_6 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_7'] = x_figi_check_digit__mutmut_7 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_8'] = x_figi_check_digit__mutmut_8 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_9'] = x_figi_check_digit__mutmut_9 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_10'] = x_figi_check_digit__mutmut_10 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_11'] = x_figi_check_digit__mutmut_11 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_12'] = x_figi_check_digit__mutmut_12 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_13'] = x_figi_check_digit__mutmut_13 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_14'] = x_figi_check_digit__mutmut_14 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_15'] = x_figi_check_digit__mutmut_15 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_16'] = x_figi_check_digit__mutmut_16 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_17'] = x_figi_check_digit__mutmut_17 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_18'] = x_figi_check_digit__mutmut_18 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_19'] = x_figi_check_digit__mutmut_19 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_20'] = x_figi_check_digit__mutmut_20 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_21'] = x_figi_check_digit__mutmut_21 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_22'] = x_figi_check_digit__mutmut_22 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_23'] = x_figi_check_digit__mutmut_23 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_24'] = x_figi_check_digit__mutmut_24 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_25'] = x_figi_check_digit__mutmut_25 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_26'] = x_figi_check_digit__mutmut_26 # type: ignore # mutmut generated
mutants_x_figi_check_digit__mutmut['x_figi_check_digit__mutmut_27'] = x_figi_check_digit__mutmut_27 # type: ignore # mutmut generated
mutants_x_validate_figi__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_validate_figi__mutmut)
def validate_figi(text: str) -> str:
    candidate = text.strip().upper()
    if not _FIGI_RE.match(candidate):
        raise ValueError(f"not a structurally valid FIGI: {text!r}")
    if figi_check_digit(candidate[:11]) != int(candidate[11]):
        raise ValueError(f"FIGI check digit mismatch: {text!r}")
    return candidate


def x_validate_figi__mutmut_orig(text: str) -> str:
    candidate = text.strip().upper()
    if not _FIGI_RE.match(candidate):
        raise ValueError(f"not a structurally valid FIGI: {text!r}")
    if figi_check_digit(candidate[:11]) != int(candidate[11]):
        raise ValueError(f"FIGI check digit mismatch: {text!r}")
    return candidate


def x_validate_figi__mutmut_1(text: str) -> str:
    candidate = None
    if not _FIGI_RE.match(candidate):
        raise ValueError(f"not a structurally valid FIGI: {text!r}")
    if figi_check_digit(candidate[:11]) != int(candidate[11]):
        raise ValueError(f"FIGI check digit mismatch: {text!r}")
    return candidate


def x_validate_figi__mutmut_2(text: str) -> str:
    candidate = text.strip().lower()
    if not _FIGI_RE.match(candidate):
        raise ValueError(f"not a structurally valid FIGI: {text!r}")
    if figi_check_digit(candidate[:11]) != int(candidate[11]):
        raise ValueError(f"FIGI check digit mismatch: {text!r}")
    return candidate


def x_validate_figi__mutmut_3(text: str) -> str:
    candidate = text.strip().upper()
    if _FIGI_RE.match(candidate):
        raise ValueError(f"not a structurally valid FIGI: {text!r}")
    if figi_check_digit(candidate[:11]) != int(candidate[11]):
        raise ValueError(f"FIGI check digit mismatch: {text!r}")
    return candidate


def x_validate_figi__mutmut_4(text: str) -> str:
    candidate = text.strip().upper()
    if not _FIGI_RE.match(None):
        raise ValueError(f"not a structurally valid FIGI: {text!r}")
    if figi_check_digit(candidate[:11]) != int(candidate[11]):
        raise ValueError(f"FIGI check digit mismatch: {text!r}")
    return candidate


def x_validate_figi__mutmut_5(text: str) -> str:
    candidate = text.strip().upper()
    if not _FIGI_RE.match(candidate):
        raise ValueError(None)
    if figi_check_digit(candidate[:11]) != int(candidate[11]):
        raise ValueError(f"FIGI check digit mismatch: {text!r}")
    return candidate


def x_validate_figi__mutmut_6(text: str) -> str:
    candidate = text.strip().upper()
    if not _FIGI_RE.match(candidate):
        raise ValueError(f"not a structurally valid FIGI: {text!r}")
    if figi_check_digit(None) != int(candidate[11]):
        raise ValueError(f"FIGI check digit mismatch: {text!r}")
    return candidate


def x_validate_figi__mutmut_7(text: str) -> str:
    candidate = text.strip().upper()
    if not _FIGI_RE.match(candidate):
        raise ValueError(f"not a structurally valid FIGI: {text!r}")
    if figi_check_digit(candidate[:12]) != int(candidate[11]):
        raise ValueError(f"FIGI check digit mismatch: {text!r}")
    return candidate


def x_validate_figi__mutmut_8(text: str) -> str:
    candidate = text.strip().upper()
    if not _FIGI_RE.match(candidate):
        raise ValueError(f"not a structurally valid FIGI: {text!r}")
    if figi_check_digit(candidate[:11]) == int(candidate[11]):
        raise ValueError(f"FIGI check digit mismatch: {text!r}")
    return candidate


def x_validate_figi__mutmut_9(text: str) -> str:
    candidate = text.strip().upper()
    if not _FIGI_RE.match(candidate):
        raise ValueError(f"not a structurally valid FIGI: {text!r}")
    if figi_check_digit(candidate[:11]) != int(None):
        raise ValueError(f"FIGI check digit mismatch: {text!r}")
    return candidate


def x_validate_figi__mutmut_10(text: str) -> str:
    candidate = text.strip().upper()
    if not _FIGI_RE.match(candidate):
        raise ValueError(f"not a structurally valid FIGI: {text!r}")
    if figi_check_digit(candidate[:11]) != int(candidate[12]):
        raise ValueError(f"FIGI check digit mismatch: {text!r}")
    return candidate


def x_validate_figi__mutmut_11(text: str) -> str:
    candidate = text.strip().upper()
    if not _FIGI_RE.match(candidate):
        raise ValueError(f"not a structurally valid FIGI: {text!r}")
    if figi_check_digit(candidate[:11]) != int(candidate[11]):
        raise ValueError(None)
    return candidate

mutants_x_validate_figi__mutmut['_mutmut_orig'] = x_validate_figi__mutmut_orig # type: ignore # mutmut generated
mutants_x_validate_figi__mutmut['x_validate_figi__mutmut_1'] = x_validate_figi__mutmut_1 # type: ignore # mutmut generated
mutants_x_validate_figi__mutmut['x_validate_figi__mutmut_2'] = x_validate_figi__mutmut_2 # type: ignore # mutmut generated
mutants_x_validate_figi__mutmut['x_validate_figi__mutmut_3'] = x_validate_figi__mutmut_3 # type: ignore # mutmut generated
mutants_x_validate_figi__mutmut['x_validate_figi__mutmut_4'] = x_validate_figi__mutmut_4 # type: ignore # mutmut generated
mutants_x_validate_figi__mutmut['x_validate_figi__mutmut_5'] = x_validate_figi__mutmut_5 # type: ignore # mutmut generated
mutants_x_validate_figi__mutmut['x_validate_figi__mutmut_6'] = x_validate_figi__mutmut_6 # type: ignore # mutmut generated
mutants_x_validate_figi__mutmut['x_validate_figi__mutmut_7'] = x_validate_figi__mutmut_7 # type: ignore # mutmut generated
mutants_x_validate_figi__mutmut['x_validate_figi__mutmut_8'] = x_validate_figi__mutmut_8 # type: ignore # mutmut generated
mutants_x_validate_figi__mutmut['x_validate_figi__mutmut_9'] = x_validate_figi__mutmut_9 # type: ignore # mutmut generated
mutants_x_validate_figi__mutmut['x_validate_figi__mutmut_10'] = x_validate_figi__mutmut_10 # type: ignore # mutmut generated
mutants_x_validate_figi__mutmut['x_validate_figi__mutmut_11'] = x_validate_figi__mutmut_11 # type: ignore # mutmut generated
mutants_x_validate_lei__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_validate_lei__mutmut)
def validate_lei(text: str) -> str:
    """ISO 17442: letters mapped A=10..Z=35, whole number mod 97 must equal 1."""
    candidate = text.strip().upper()
    if not _LEI_RE.match(candidate):
        raise ValueError(f"not a structurally valid LEI: {text!r}")
    numeric = "".join(str(_char_value(ch)) for ch in candidate)
    if int(numeric) % 97 != 1:
        raise ValueError(f"LEI checksum mismatch: {text!r}")
    return candidate


def x_validate_lei__mutmut_orig(text: str) -> str:
    """ISO 17442: letters mapped A=10..Z=35, whole number mod 97 must equal 1."""
    candidate = text.strip().upper()
    if not _LEI_RE.match(candidate):
        raise ValueError(f"not a structurally valid LEI: {text!r}")
    numeric = "".join(str(_char_value(ch)) for ch in candidate)
    if int(numeric) % 97 != 1:
        raise ValueError(f"LEI checksum mismatch: {text!r}")
    return candidate


def x_validate_lei__mutmut_1(text: str) -> str:
    """ISO 17442: letters mapped A=10..Z=35, whole number mod 97 must equal 1."""
    candidate = None
    if not _LEI_RE.match(candidate):
        raise ValueError(f"not a structurally valid LEI: {text!r}")
    numeric = "".join(str(_char_value(ch)) for ch in candidate)
    if int(numeric) % 97 != 1:
        raise ValueError(f"LEI checksum mismatch: {text!r}")
    return candidate


def x_validate_lei__mutmut_2(text: str) -> str:
    """ISO 17442: letters mapped A=10..Z=35, whole number mod 97 must equal 1."""
    candidate = text.strip().lower()
    if not _LEI_RE.match(candidate):
        raise ValueError(f"not a structurally valid LEI: {text!r}")
    numeric = "".join(str(_char_value(ch)) for ch in candidate)
    if int(numeric) % 97 != 1:
        raise ValueError(f"LEI checksum mismatch: {text!r}")
    return candidate


def x_validate_lei__mutmut_3(text: str) -> str:
    """ISO 17442: letters mapped A=10..Z=35, whole number mod 97 must equal 1."""
    candidate = text.strip().upper()
    if _LEI_RE.match(candidate):
        raise ValueError(f"not a structurally valid LEI: {text!r}")
    numeric = "".join(str(_char_value(ch)) for ch in candidate)
    if int(numeric) % 97 != 1:
        raise ValueError(f"LEI checksum mismatch: {text!r}")
    return candidate


def x_validate_lei__mutmut_4(text: str) -> str:
    """ISO 17442: letters mapped A=10..Z=35, whole number mod 97 must equal 1."""
    candidate = text.strip().upper()
    if not _LEI_RE.match(None):
        raise ValueError(f"not a structurally valid LEI: {text!r}")
    numeric = "".join(str(_char_value(ch)) for ch in candidate)
    if int(numeric) % 97 != 1:
        raise ValueError(f"LEI checksum mismatch: {text!r}")
    return candidate


def x_validate_lei__mutmut_5(text: str) -> str:
    """ISO 17442: letters mapped A=10..Z=35, whole number mod 97 must equal 1."""
    candidate = text.strip().upper()
    if not _LEI_RE.match(candidate):
        raise ValueError(None)
    numeric = "".join(str(_char_value(ch)) for ch in candidate)
    if int(numeric) % 97 != 1:
        raise ValueError(f"LEI checksum mismatch: {text!r}")
    return candidate


def x_validate_lei__mutmut_6(text: str) -> str:
    """ISO 17442: letters mapped A=10..Z=35, whole number mod 97 must equal 1."""
    candidate = text.strip().upper()
    if not _LEI_RE.match(candidate):
        raise ValueError(f"not a structurally valid LEI: {text!r}")
    numeric = None
    if int(numeric) % 97 != 1:
        raise ValueError(f"LEI checksum mismatch: {text!r}")
    return candidate


def x_validate_lei__mutmut_7(text: str) -> str:
    """ISO 17442: letters mapped A=10..Z=35, whole number mod 97 must equal 1."""
    candidate = text.strip().upper()
    if not _LEI_RE.match(candidate):
        raise ValueError(f"not a structurally valid LEI: {text!r}")
    numeric = "".join(None)
    if int(numeric) % 97 != 1:
        raise ValueError(f"LEI checksum mismatch: {text!r}")
    return candidate


def x_validate_lei__mutmut_8(text: str) -> str:
    """ISO 17442: letters mapped A=10..Z=35, whole number mod 97 must equal 1."""
    candidate = text.strip().upper()
    if not _LEI_RE.match(candidate):
        raise ValueError(f"not a structurally valid LEI: {text!r}")
    numeric = "XXXX".join(str(_char_value(ch)) for ch in candidate)
    if int(numeric) % 97 != 1:
        raise ValueError(f"LEI checksum mismatch: {text!r}")
    return candidate


def x_validate_lei__mutmut_9(text: str) -> str:
    """ISO 17442: letters mapped A=10..Z=35, whole number mod 97 must equal 1."""
    candidate = text.strip().upper()
    if not _LEI_RE.match(candidate):
        raise ValueError(f"not a structurally valid LEI: {text!r}")
    numeric = "".join(str(None) for ch in candidate)
    if int(numeric) % 97 != 1:
        raise ValueError(f"LEI checksum mismatch: {text!r}")
    return candidate


def x_validate_lei__mutmut_10(text: str) -> str:
    """ISO 17442: letters mapped A=10..Z=35, whole number mod 97 must equal 1."""
    candidate = text.strip().upper()
    if not _LEI_RE.match(candidate):
        raise ValueError(f"not a structurally valid LEI: {text!r}")
    numeric = "".join(str(_char_value(None)) for ch in candidate)
    if int(numeric) % 97 != 1:
        raise ValueError(f"LEI checksum mismatch: {text!r}")
    return candidate


def x_validate_lei__mutmut_11(text: str) -> str:
    """ISO 17442: letters mapped A=10..Z=35, whole number mod 97 must equal 1."""
    candidate = text.strip().upper()
    if not _LEI_RE.match(candidate):
        raise ValueError(f"not a structurally valid LEI: {text!r}")
    numeric = "".join(str(_char_value(ch)) for ch in candidate)
    if int(numeric) / 97 != 1:
        raise ValueError(f"LEI checksum mismatch: {text!r}")
    return candidate


def x_validate_lei__mutmut_12(text: str) -> str:
    """ISO 17442: letters mapped A=10..Z=35, whole number mod 97 must equal 1."""
    candidate = text.strip().upper()
    if not _LEI_RE.match(candidate):
        raise ValueError(f"not a structurally valid LEI: {text!r}")
    numeric = "".join(str(_char_value(ch)) for ch in candidate)
    if int(None) % 97 != 1:
        raise ValueError(f"LEI checksum mismatch: {text!r}")
    return candidate


def x_validate_lei__mutmut_13(text: str) -> str:
    """ISO 17442: letters mapped A=10..Z=35, whole number mod 97 must equal 1."""
    candidate = text.strip().upper()
    if not _LEI_RE.match(candidate):
        raise ValueError(f"not a structurally valid LEI: {text!r}")
    numeric = "".join(str(_char_value(ch)) for ch in candidate)
    if int(numeric) % 98 != 1:
        raise ValueError(f"LEI checksum mismatch: {text!r}")
    return candidate


def x_validate_lei__mutmut_14(text: str) -> str:
    """ISO 17442: letters mapped A=10..Z=35, whole number mod 97 must equal 1."""
    candidate = text.strip().upper()
    if not _LEI_RE.match(candidate):
        raise ValueError(f"not a structurally valid LEI: {text!r}")
    numeric = "".join(str(_char_value(ch)) for ch in candidate)
    if int(numeric) % 97 == 1:
        raise ValueError(f"LEI checksum mismatch: {text!r}")
    return candidate


def x_validate_lei__mutmut_15(text: str) -> str:
    """ISO 17442: letters mapped A=10..Z=35, whole number mod 97 must equal 1."""
    candidate = text.strip().upper()
    if not _LEI_RE.match(candidate):
        raise ValueError(f"not a structurally valid LEI: {text!r}")
    numeric = "".join(str(_char_value(ch)) for ch in candidate)
    if int(numeric) % 97 != 2:
        raise ValueError(f"LEI checksum mismatch: {text!r}")
    return candidate


def x_validate_lei__mutmut_16(text: str) -> str:
    """ISO 17442: letters mapped A=10..Z=35, whole number mod 97 must equal 1."""
    candidate = text.strip().upper()
    if not _LEI_RE.match(candidate):
        raise ValueError(f"not a structurally valid LEI: {text!r}")
    numeric = "".join(str(_char_value(ch)) for ch in candidate)
    if int(numeric) % 97 != 1:
        raise ValueError(None)
    return candidate

mutants_x_validate_lei__mutmut['_mutmut_orig'] = x_validate_lei__mutmut_orig # type: ignore # mutmut generated
mutants_x_validate_lei__mutmut['x_validate_lei__mutmut_1'] = x_validate_lei__mutmut_1 # type: ignore # mutmut generated
mutants_x_validate_lei__mutmut['x_validate_lei__mutmut_2'] = x_validate_lei__mutmut_2 # type: ignore # mutmut generated
mutants_x_validate_lei__mutmut['x_validate_lei__mutmut_3'] = x_validate_lei__mutmut_3 # type: ignore # mutmut generated
mutants_x_validate_lei__mutmut['x_validate_lei__mutmut_4'] = x_validate_lei__mutmut_4 # type: ignore # mutmut generated
mutants_x_validate_lei__mutmut['x_validate_lei__mutmut_5'] = x_validate_lei__mutmut_5 # type: ignore # mutmut generated
mutants_x_validate_lei__mutmut['x_validate_lei__mutmut_6'] = x_validate_lei__mutmut_6 # type: ignore # mutmut generated
mutants_x_validate_lei__mutmut['x_validate_lei__mutmut_7'] = x_validate_lei__mutmut_7 # type: ignore # mutmut generated
mutants_x_validate_lei__mutmut['x_validate_lei__mutmut_8'] = x_validate_lei__mutmut_8 # type: ignore # mutmut generated
mutants_x_validate_lei__mutmut['x_validate_lei__mutmut_9'] = x_validate_lei__mutmut_9 # type: ignore # mutmut generated
mutants_x_validate_lei__mutmut['x_validate_lei__mutmut_10'] = x_validate_lei__mutmut_10 # type: ignore # mutmut generated
mutants_x_validate_lei__mutmut['x_validate_lei__mutmut_11'] = x_validate_lei__mutmut_11 # type: ignore # mutmut generated
mutants_x_validate_lei__mutmut['x_validate_lei__mutmut_12'] = x_validate_lei__mutmut_12 # type: ignore # mutmut generated
mutants_x_validate_lei__mutmut['x_validate_lei__mutmut_13'] = x_validate_lei__mutmut_13 # type: ignore # mutmut generated
mutants_x_validate_lei__mutmut['x_validate_lei__mutmut_14'] = x_validate_lei__mutmut_14 # type: ignore # mutmut generated
mutants_x_validate_lei__mutmut['x_validate_lei__mutmut_15'] = x_validate_lei__mutmut_15 # type: ignore # mutmut generated
mutants_x_validate_lei__mutmut['x_validate_lei__mutmut_16'] = x_validate_lei__mutmut_16 # type: ignore # mutmut generated


class Figi(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: str

    @field_validator("value")
    @classmethod
    def _check(cls, v: str) -> str:
        return validate_figi(v)

    def __str__(self) -> str:
        return self.value


class Lei(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: str

    @field_validator("value")
    @classmethod
    def _check(cls, v: str) -> str:
        return validate_lei(v)

    def __str__(self) -> str:
        return self.value
mutants_xǁSecurityQueryǁdisplay__mutmut: MutantDict = {}  # type: ignore


class SecurityQuery(BaseModel):
    """A parsed human-facing security reference, before master resolution.

    ``IBM US Equity`` -> ticker=IBM, venue=US, key=EQUITY.
    ``IBM 4.15 05/15/39 Corp`` -> ticker=IBM, descriptor="4.15 05/15/39", key=CORP.
    The venue/composite code and the free-text descriptor are namespace-specific;
    resolution against the security master happens in the master, not here.
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    key: YellowKey
    venue: str | None = None
    descriptor: str | None = None

    @_mutmut_mutated(mutants_xǁSecurityQueryǁdisplay__mutmut)
    def display(self) -> str:
        parts = [self.ticker]
        if self.descriptor:
            parts.append(self.descriptor)
        if self.venue:
            parts.append(self.venue)
        parts.append(self.key.value)
        return " ".join(parts)

    def xǁSecurityQueryǁdisplay__mutmut_orig(self) -> str:
        parts = [self.ticker]
        if self.descriptor:
            parts.append(self.descriptor)
        if self.venue:
            parts.append(self.venue)
        parts.append(self.key.value)
        return " ".join(parts)

    def xǁSecurityQueryǁdisplay__mutmut_1(self) -> str:
        parts = None
        if self.descriptor:
            parts.append(self.descriptor)
        if self.venue:
            parts.append(self.venue)
        parts.append(self.key.value)
        return " ".join(parts)

    def xǁSecurityQueryǁdisplay__mutmut_2(self) -> str:
        parts = [self.ticker]
        if self.descriptor:
            parts.append(None)
        if self.venue:
            parts.append(self.venue)
        parts.append(self.key.value)
        return " ".join(parts)

    def xǁSecurityQueryǁdisplay__mutmut_3(self) -> str:
        parts = [self.ticker]
        if self.descriptor:
            parts.append(self.descriptor)
        if self.venue:
            parts.append(None)
        parts.append(self.key.value)
        return " ".join(parts)

    def xǁSecurityQueryǁdisplay__mutmut_4(self) -> str:
        parts = [self.ticker]
        if self.descriptor:
            parts.append(self.descriptor)
        if self.venue:
            parts.append(self.venue)
        parts.append(None)
        return " ".join(parts)

    def xǁSecurityQueryǁdisplay__mutmut_5(self) -> str:
        parts = [self.ticker]
        if self.descriptor:
            parts.append(self.descriptor)
        if self.venue:
            parts.append(self.venue)
        parts.append(self.key.value)
        return " ".join(None)

    def xǁSecurityQueryǁdisplay__mutmut_6(self) -> str:
        parts = [self.ticker]
        if self.descriptor:
            parts.append(self.descriptor)
        if self.venue:
            parts.append(self.venue)
        parts.append(self.key.value)
        return "XX XX".join(parts)

mutants_xǁSecurityQueryǁdisplay__mutmut['_mutmut_orig'] = SecurityQuery.xǁSecurityQueryǁdisplay__mutmut_orig # type: ignore # mutmut generated
mutants_xǁSecurityQueryǁdisplay__mutmut['xǁSecurityQueryǁdisplay__mutmut_1'] = SecurityQuery.xǁSecurityQueryǁdisplay__mutmut_1 # type: ignore # mutmut generated
mutants_xǁSecurityQueryǁdisplay__mutmut['xǁSecurityQueryǁdisplay__mutmut_2'] = SecurityQuery.xǁSecurityQueryǁdisplay__mutmut_2 # type: ignore # mutmut generated
mutants_xǁSecurityQueryǁdisplay__mutmut['xǁSecurityQueryǁdisplay__mutmut_3'] = SecurityQuery.xǁSecurityQueryǁdisplay__mutmut_3 # type: ignore # mutmut generated
mutants_xǁSecurityQueryǁdisplay__mutmut['xǁSecurityQueryǁdisplay__mutmut_4'] = SecurityQuery.xǁSecurityQueryǁdisplay__mutmut_4 # type: ignore # mutmut generated
mutants_xǁSecurityQueryǁdisplay__mutmut['xǁSecurityQueryǁdisplay__mutmut_5'] = SecurityQuery.xǁSecurityQueryǁdisplay__mutmut_5 # type: ignore # mutmut generated
mutants_xǁSecurityQueryǁdisplay__mutmut['xǁSecurityQueryǁdisplay__mutmut_6'] = SecurityQuery.xǁSecurityQueryǁdisplay__mutmut_6 # type: ignore # mutmut generated


_VENUE_RE = re.compile(r"^[A-Z]{1,3}$")
mutants_x_parse_security__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_parse_security__mutmut)
def parse_security(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_orig(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_1(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = None
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_2(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) <= 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_3(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 3:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_4(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(None)
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_5(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = None
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_6(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(None)
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_7(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[+1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_8(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-2])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_9(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = None
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_10(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[1]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_11(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = None
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_12(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[2:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_13(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:+1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_14(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-2]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_15(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = ""
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_16(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = ""
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_17(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1 or _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_18(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD) or len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_19(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key not in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_20(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) != 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_21(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 2
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_22(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(None)
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_23(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[+1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_24(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-2])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_25(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = None
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_26(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[+1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_27(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-2]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_28(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = None
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_29(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(None)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_30(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = "XX XX".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_31(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=None, key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_32(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=None, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_33(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=None, descriptor=descriptor)


def x_parse_security__mutmut_34(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, descriptor=None)


def x_parse_security__mutmut_35(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(key=key, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_36(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, venue=venue, descriptor=descriptor)


def x_parse_security__mutmut_37(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, descriptor=descriptor)


def x_parse_security__mutmut_38(text: str) -> SecurityQuery:
    """Parse ``<ticker> [descriptor|venue] <yellow key>`` (spec §5.1 forms).

    The last token must be a yellow key. A single all-caps 1-3 letter token
    immediately before it is a venue/composite code for listed namespaces;
    everything else between ticker and key is the descriptor (coupon/maturity
    for bonds, tenor words for OTC tickers).
    """
    tokens = text.strip().split()
    if len(tokens) < 2:
        raise ValueError(f"not a security reference: {text!r}")
    key = YellowKey.parse(tokens[-1])
    ticker = tokens[0]
    middle = tokens[1:-1]
    venue: str | None = None
    descriptor: str | None = None
    if middle:
        if (
            key in (YellowKey.EQUITY, YellowKey.INDEX, YellowKey.PFD)
            and len(middle) == 1
            and _VENUE_RE.match(middle[-1])
        ):
            venue = middle[-1]
        else:
            descriptor = " ".join(middle)
    return SecurityQuery(ticker=ticker, key=key, venue=venue, )

mutants_x_parse_security__mutmut['_mutmut_orig'] = x_parse_security__mutmut_orig # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_1'] = x_parse_security__mutmut_1 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_2'] = x_parse_security__mutmut_2 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_3'] = x_parse_security__mutmut_3 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_4'] = x_parse_security__mutmut_4 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_5'] = x_parse_security__mutmut_5 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_6'] = x_parse_security__mutmut_6 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_7'] = x_parse_security__mutmut_7 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_8'] = x_parse_security__mutmut_8 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_9'] = x_parse_security__mutmut_9 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_10'] = x_parse_security__mutmut_10 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_11'] = x_parse_security__mutmut_11 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_12'] = x_parse_security__mutmut_12 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_13'] = x_parse_security__mutmut_13 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_14'] = x_parse_security__mutmut_14 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_15'] = x_parse_security__mutmut_15 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_16'] = x_parse_security__mutmut_16 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_17'] = x_parse_security__mutmut_17 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_18'] = x_parse_security__mutmut_18 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_19'] = x_parse_security__mutmut_19 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_20'] = x_parse_security__mutmut_20 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_21'] = x_parse_security__mutmut_21 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_22'] = x_parse_security__mutmut_22 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_23'] = x_parse_security__mutmut_23 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_24'] = x_parse_security__mutmut_24 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_25'] = x_parse_security__mutmut_25 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_26'] = x_parse_security__mutmut_26 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_27'] = x_parse_security__mutmut_27 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_28'] = x_parse_security__mutmut_28 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_29'] = x_parse_security__mutmut_29 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_30'] = x_parse_security__mutmut_30 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_31'] = x_parse_security__mutmut_31 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_32'] = x_parse_security__mutmut_32 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_33'] = x_parse_security__mutmut_33 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_34'] = x_parse_security__mutmut_34 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_35'] = x_parse_security__mutmut_35 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_36'] = x_parse_security__mutmut_36 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_37'] = x_parse_security__mutmut_37 # type: ignore # mutmut generated
mutants_x_parse_security__mutmut['x_parse_security__mutmut_38'] = x_parse_security__mutmut_38 # type: ignore # mutmut generated
