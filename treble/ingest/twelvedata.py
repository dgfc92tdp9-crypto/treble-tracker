"""Twelve Data daily equity prices (spec §9.1, §16 — the `PORT` factor model).

**Why this source, and what was ruled out first.** `PORT`'s factor model
needs per-name return history, and this repository had none: N-PORT gives
holdings quarterly, which caps a name at ~28 observations against a
1,260-day window. Yahoo breaches its terms, Stooq serves a proof-of-work
bot challenge that this project will not defeat, and FINRA Gateway's user
agreement prohibits automated access outright. What remained was
registration-gated, which is a decision only the operator can make; Jack
provisioned the key.

**What was measured before a line of this was written**, because a price
source that is wrong in a systematic way produces a factor model that is
confidently wrong:

- **Depth.** 5,000 daily rows in one call; IBM back to 2006-09-20. Four
  times the window the model asks for.
- **Splits.** NVDA across its 2024-06-10 ten-for-one reads 120.89 into
  121.79 — a +0.7% overnight move, not the -90% a raw series would put into
  the panel. That is the single most destructive defect a return panel can
  carry, because it looks like an event rather than an error.
- **Dividends.** The series is total-return, established without an external
  anchor: raw exchange prices are quantised to whole cents, an adjusted
  series is not. IBM in 2006 has 0 of 164 OHLC values on exact cents;
  IBM in 2026 has 52%. No split since 1999 means the 2006 scaling can only
  be dividends. This matters because Ken French's factors are total-return
  too — regressing price-only returns against them would push dividend
  yield into alpha, largest for high-yield names, where it reads as skill.
- **Rate limit.** Eight requests per minute, found by hitting it and
  recovering, not read off a pricing page.

**Redistribution is restricted and the flag is not decorative.** The free
tier is licensed for personal use only. `redistribution_restricted=True`
routes these facts into the bulk-export guard, which is what stops them
reaching the ALLQ contribution API or a federated node.

**The key never enters a fact, a URI or a log line.** `source_uri` is built
without it, so the payload store, the ingest log and every provenance record
are safe to read and safe to share. A credential in a stored URI is
permanent: it survives replay, and I5 means replay is the point.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, date, datetime

from treble.core.facts import Fact
from treble.core.provenance import ExtractionMethod, Provenance
from treble.ingest.base import ParsedBatch, RawPayload, SourceAdapter, SourceMeta
from treble.store.payloads import PayloadHash

API_URL = "https://api.twelvedata.com/time_series"

#: Environment variable holding the API key. Read at fetch time rather than
#: import time so that importing this module never requires a credential —
#: the parser, and therefore replay, works without one.
API_KEY_ENV = "TWELVEDATA_API_KEY"

#: Rows per request. The documented maximum, because the free tier's limit
#: is requests rather than rows: asking for fewer would cost more calls for
#: less history.
MAX_OUTPUTSIZE = 5000

#: Requests per second. Eight per minute, measured. Expressed per-second
#: because that is what `TokenBucket` takes, and deliberately a shade under
#: 8/60 so a burst at a minute boundary does not trip the limit.
RATE_PER_SECOND = 0.12

#: Fields carried through from the vendor's own names. `close` is
#: total-return adjusted (see the module docstring); it is stored under a
#: name that says so, because a column called CLOSE that is silently a total
#: return is the kind of thing a later reader regresses without checking.
PRICE_FIELD = "ADJ_CLOSE"
VOLUME_FIELD = "VOLUME"


class TwelveDataError(RuntimeError):
    """The vendor reported an error, with its own message preserved."""


class TwelveDataDailyAdapter(SourceAdapter):
    """Daily total-return closes for a fixed list of symbols."""

    meta = SourceMeta(
        source_id="twelvedata",
        # daily bars; a gap means the key, the tier or the symbol changed.
        expected_cadence_days=1.0,
        description="Twelve Data daily equity time series (split and dividend adjusted)",
        licence=(
            "Free tier, personal use only: the data may not be displayed or shared "
            "with another person or organisation. Redistribution restricted."
        ),
        redistribution_restricted=True,
        rate_limit_per_second=RATE_PER_SECOND,
    )
    parser_version = "1"

    def __init__(
        self,
        payloads: object,
        log: object,
        *,
        symbols: tuple[str, ...],
        outputsize: int = MAX_OUTPUTSIZE,
    ) -> None:
        super().__init__(payloads, log)  # type: ignore[arg-type]
        if not symbols:
            raise ValueError("no symbols to fetch; an empty universe returns no history")
        self._symbols = symbols
        self._outputsize = outputsize

    def fetch(self) -> Iterator[RawPayload]:
        key = os.environ.get(API_KEY_ENV)
        if not key:
            raise TwelveDataError(
                f"{API_KEY_ENV} is not set. It lives in .env, which `treble.cmd.env` "
                "parses rather than sources — sourcing a .env executes it, and a line "
                "without a NAME= prefix becomes a command whose name is the secret."
            )
        for symbol in self._symbols:
            # `_get` throttles and retries a truncated or rate-limited
            # response. Forty-five symbols at eight requests a minute is six
            # unbroken minutes of calls, and before this a single dropped
            # connection ended the whole source — twice in a row, on
            # 2026-09-01, after fifteen symbols had already been stored.
            response = self._get(
                API_URL,
                params={
                    "symbol": symbol,
                    "interval": "1day",
                    "outputsize": str(self._outputsize),
                    "apikey": key,
                },
            )
            yield RawPayload(
                data=response.content,
                # Built without the key. `str(response.url)` would carry it
                # into the payload store and the ingest log, where it would
                # survive every replay.
                source_uri=(
                    f"{API_URL}?symbol={symbol}&interval=1day&outputsize={self._outputsize}"
                ),
                fetched_at=datetime.now(UTC),
            )

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        document = json.loads(payload.data)
        if "values" not in document:
            raise TwelveDataError(
                f"no time series in the response: {document.get('message', document)!r}"
            )
        meta = document.get("meta", {})
        symbol = meta.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise TwelveDataError(
                "the response carries no symbol, so its rows cannot be attributed. "
                "Taking the symbol from the request instead would let a vendor-side "
                "mix-up file one instrument's prices under another's subject"
            )

        provenance = Provenance(
            source_system=self.meta.source_id,
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.API,
            extractor_version=self.parser_version,
            payload_hash=str(payload_hash),
        )

        subject = f"equity:{symbol}"
        facts: list[Fact] = []
        for row in document["values"]:
            day = self._day(row.get("datetime"))
            close = self._number(row.get("close"))
            if day is None or close is None:
                # A row without a date or a close is not a zero-price day; it
                # is a row this parser does not understand, and inventing a
                # value for it would put a fabricated return in the panel.
                continue
            facts.append(
                Fact(
                    subject=subject,
                    field=PRICE_FIELD,
                    value=close,
                    effective_from=day,
                    effective_to=day,
                    # The vendor stamps no publication time, so the knowledge
                    # date is when this was retrieved — never the wall clock
                    # at parse time, which would make replay non-deterministic.
                    knowledge_from=payload.fetched_at,
                    provenance_id=provenance.id,
                )
            )
            volume = self._number(row.get("volume"))
            if volume is not None:
                facts.append(
                    Fact(
                        subject=subject,
                        field=VOLUME_FIELD,
                        value=volume,
                        effective_from=day,
                        effective_to=day,
                        knowledge_from=payload.fetched_at,
                        provenance_id=provenance.id,
                    )
                )
        if not facts:
            raise TwelveDataError(
                f"{symbol}: the response parsed but produced no usable rows. An empty "
                "series and an unparsed one render the same and mean different things"
            )
        return ParsedBatch(provenance=(provenance,), facts=tuple(facts))

    @staticmethod
    def _day(value: object) -> date | None:
        if not isinstance(value, str):
            return None
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None

    @staticmethod
    def _number(value: object) -> float | None:
        if not isinstance(value, str | int | float):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


__all__ = [
    "API_KEY_ENV",
    "API_URL",
    "MAX_OUTPUTSIZE",
    "PRICE_FIELD",
    "RATE_PER_SECOND",
    "VOLUME_FIELD",
    "TwelveDataDailyAdapter",
    "TwelveDataError",
]
