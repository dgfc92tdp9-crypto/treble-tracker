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


def new_tuid() -> TUID:
    return TUID(uuid.uuid4().hex)


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
    def parse(cls, text: str) -> YellowKey:
        normalised = text.strip().upper().replace("-", "")
        for key in cls:
            if normalised in (key.name.replace("_", ""), key.value.upper().replace("-", "")):
                return key
        raise ValueError(f"unknown yellow key: {text!r}")


# FIGI: 12 chars — two consonants, 'G', eight consonant/digit, one check digit
# (ANSI X9.145). Vowels are excluded throughout.
_FIGI_RE = re.compile(r"^[B-DF-HJ-NP-TV-Z0-9]{2}G[B-DF-HJ-NP-TV-Z0-9]{8}[0-9]$")
# LEI: ISO 17442 — 18 alphanumeric + 2 check digits, validated mod 97-10.
_LEI_RE = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")


def _char_value(ch: str) -> int:
    """0-9 -> 0-9, A-Z -> 10-35 (shared by the FIGI and LEI check algorithms)."""
    if ch.isdigit():
        return int(ch)
    return ord(ch) - ord("A") + 10


#: Values filers write where an identifier does not exist. They are not
#: identifiers and must never key a subject or form a link.
#:
#: Defined here rather than in an adapter because both layers need the same
#: answer and they had different ones: `ingest/nport.py` refused these when
#: keying subjects while `core/master.py` happily built entity-graph links
#: from them, so 246 unrelated instruments were linked through
#: `cusip:000000000`. Fixing the adapter fixed the instance; this fixes the
#: class, by leaving one definition for anything that needs to ask.
PLACEHOLDER_IDENTIFIERS: frozenset[str] = frozenset(
    {"", "N/A", "NA", "N.A.", "NONE", "NULL", "UNKNOWN", "000000000", "0"}
)


def is_placeholder_identifier(text: object) -> bool:
    """Whether a value is a filer's stand-in rather than an identifier."""
    if not isinstance(text, str):
        return text is None
    return text.strip().upper() in PLACEHOLDER_IDENTIFIERS


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


def validate_figi(text: str) -> str:
    candidate = text.strip().upper()
    if not _FIGI_RE.match(candidate):
        raise ValueError(f"not a structurally valid FIGI: {text!r}")
    if figi_check_digit(candidate[:11]) != int(candidate[11]):
        raise ValueError(f"FIGI check digit mismatch: {text!r}")
    return candidate


def validate_lei(text: str) -> str:
    """ISO 17442: letters mapped A=10..Z=35, whole number mod 97 must equal 1."""
    candidate = text.strip().upper()
    if not _LEI_RE.match(candidate):
        raise ValueError(f"not a structurally valid LEI: {text!r}")
    numeric = "".join(str(_char_value(ch)) for ch in candidate)
    if int(numeric) % 97 != 1:
        raise ValueError(f"LEI checksum mismatch: {text!r}")
    return candidate


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

    def display(self) -> str:
        parts = [self.ticker]
        if self.descriptor:
            parts.append(self.descriptor)
        if self.venue:
            parts.append(self.venue)
        parts.append(self.key.value)
        return " ".join(parts)


_VENUE_RE = re.compile(r"^[A-Z]{1,3}$")


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
