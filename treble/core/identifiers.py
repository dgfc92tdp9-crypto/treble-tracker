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


def isin_check_digit(body: str) -> str:
    """The Luhn check digit for an 11-character ISIN body.

    Letters expand to two digits (A=10 … Z=35) *before* the Luhn pass, not
    after — expanding afterwards changes which digits fall in the doubled
    positions and produces a plausible wrong answer for exactly the ISINs
    containing letters, which is most of them.
    """
    digits = "".join(str(int(c, 36)) for c in body.upper())
    total = 0
    # Doubling applies from the rightmost digit of the *body*, because the
    # check digit that will sit to its right is not yet present.
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 0:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return str((10 - total % 10) % 10)


def isin_from_cusip(cusip: str, *, country: str = "US") -> str:
    """The ISIN a CUSIP sits inside.

    A US or Canadian ISIN is the country code, the nine-character CUSIP and
    a check digit — so the two identifiers are the same instrument written
    two ways, and a store that holds one can answer for the other. This
    matters here because N-PORT publishes ISINs and a trader types CUSIPs:
    without the bridge, 1,861 stored bonds were addressable only by an
    identifier the filings do not carry.
    """
    body = f"{country.upper()}{cusip.upper()}"
    return body + isin_check_digit(body)


def cusip_from_isin(isin: str) -> str | None:
    """The CUSIP inside a US or Canadian ISIN, or None.

    Only US and CA: elsewhere the nine characters after the country code
    are a national number that is not a CUSIP, and returning one anyway
    would produce an identifier that looks right and refers to nothing.
    """
    value = isin.strip().upper()
    if len(value) != 12 or value[:2] not in {"US", "CA"}:
        return None
    return value[2:11]


def looks_like_isin(value: str) -> bool:
    """Whether a string is shaped like an ISIN and passes its check digit.

    The check digit is verified rather than assumed. A twelve-character
    typo is otherwise indistinguishable from an ISIN, and resolves to a
    subject with no facts — a screen of dashes that reads as "no data for
    this bond" rather than "you mistyped it".
    """
    candidate = value.strip().upper()
    if len(candidate) != 12 or not candidate[:2].isalpha() or not candidate.isalnum():
        return False
    return isin_check_digit(candidate[:11]) == candidate[11]


#: Namespace for a *position* — one fund's holding of one instrument — as
#: distinct from the instrument itself.
POSITION_PREFIX = "pos:"


def position_subject(*, fund: str, instrument: TUID | str) -> TUID:
    """Key one fund's holding of one instrument.

    An instrument subject answers "what is this bond": its maturity, coupon,
    issuer and denomination are the same facts whoever holds it. A *position*
    answers "how much of it does this fund own, and at what mark", and that
    is a different question with a different answer per fund.

    Keying both to `isin:` conflated them. Three funds reported
    `isin:US7185461040` for 2026-03-31 at $1.87bn, $35.0m and $4.39m; the
    visibility window partitions on subject and field and shows one row, so
    the store held all three and every screen saw $4.39m — the smallest,
    because its filing happened to be fetched last. Measured on the live
    store: 75 (instrument, period) pairs reported by more than one filing,
    376 values invisible.

    That loss never appeared in `DuckStore.ambiguous_partitions`, and the
    reason is worth keeping: that check groups by `knowledge_from` as well,
    so it only sees values that collide at the *same* knowledge time. Two
    funds' filings are fetched seconds apart, so their marks land at
    different knowledge times and the window silently prefers the later one.
    A partition can lose data without ever being ambiguous.

    Raises when the fund is unnamed rather than falling back to the bare
    instrument, which would put the position back on the key it is being
    moved off.

    **Instrument first, fund last.** Every reader asks "what is held in this
    bond" and none asks "what does this fund hold", so the instrument leads
    and `pos:isin:US7185461040:` prefix-matches every fund reporting it.
    Fund-first would have forced a full sweep of the store for one bond, and
    the callers that need this are inside per-bond loops.
    """
    scope = " ".join(str(fund).split()).upper().replace(":", "-").replace(" ", "_")
    if not scope:
        raise ValueError("a position must name the fund that holds it")
    return TUID(f"{POSITION_PREFIX}{instrument}:{scope}")


def position_prefix_for(instrument: TUID | str) -> str:
    """The prefix matching every fund's position in one instrument."""
    return f"{POSITION_PREFIX}{instrument}:"


def parse_position_subject(subject: TUID | str) -> tuple[str, TUID] | None:
    """The fund and instrument behind a position subject, or None.

    `None` rather than a raise: readers sweep every subject in the store and
    a non-position subject is an ordinary answer, not an error.

    Split from the right. The instrument keeps its own namespace separator
    (`isin:US7185461040`), so the *last* segment is the fund — which holds
    because `position_subject` strips colons out of the fund before joining.
    """
    text = str(subject)
    if not text.startswith(POSITION_PREFIX):
        return None
    instrument, separator, fund = text[len(POSITION_PREFIX) :].rpartition(":")
    if not separator or not fund or not instrument:
        return None
    return fund, TUID(instrument)
