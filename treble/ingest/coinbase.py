"""Coinbase Exchange — daily crypto candles (spec §3.1 `Crypto`).

Coinbase's public market-data endpoints need no key and are the exchange's
own prints, so a price here comes from the venue that matched the trade
rather than from an aggregator averaging several.

**Why an exchange rather than an index.** Crypto has no consolidated tape:
the same asset trades at materially different prices across venues at the
same instant. An aggregate hides that behind one number; naming the venue
makes the price checkable. The subject records which exchange it came from
for the same reason bond marks record which filer valued them.

**What it is not.** One venue's view, not a global price. A screen showing
it must say Coinbase, and a portfolio marked here is marked at Coinbase.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.ingest.base import ParsedBatch, RawPayload, SourceAdapter, SourceMeta, utcnow
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore

CANDLES_URL = "https://api.exchange.coinbase.com/products/{product}/candles"

#: One day, in seconds. Daily granularity only: this is the end-of-day
#: series behind GP and HP, not a tick feed, and Phase 1 has no ticker plant.
DAILY = 86_400

#: Candle tuple layout, which the API documents but does not label.
_TIME, _LOW, _HIGH, _OPEN, _CLOSE, _VOLUME = range(6)


def crypto_subject(product: str) -> TUID:
    """`BTC-USD` -> `crypto:coinbase:BTC-USD`.

    The venue is part of the subject. Two exchanges' prices for one asset
    are different facts about different markets, and merging them under one
    subject would let latest-wins silently pick whichever was fetched last.
    """
    return TUID(f"crypto:coinbase:{product.upper()}")


class CoinbaseCandlesAdapter(SourceAdapter):
    """Daily OHLCV candles for the configured products."""

    meta = SourceMeta(
        source_id="coinbase",
        description="Coinbase Exchange public daily candles",
        licence="Public market data endpoints; no key, no redistribution limit stated",
        redistribution_restricted=False,
        rate_limit_per_second=3.0,
    )
    parser_version = "1"

    def __init__(
        self, payloads: PayloadStore, log: IngestLog, *, products: tuple[str, ...]
    ) -> None:
        super().__init__(payloads, log)
        self._products = products

    def fetch(self) -> Iterator[RawPayload]:
        for product in self._products:
            self._throttle()
            url = CANDLES_URL.format(product=product)
            response = httpx.get(
                url,
                params={"granularity": DAILY},
                headers={"User-Agent": "treble-tracker"},
                timeout=60.0,
                follow_redirects=True,
            )
            response.raise_for_status()
            yield RawPayload(
                data=response.content,
                # The product is carried in the URI so replay can recover the
                # subject: the payload itself is a bare array of numbers with
                # nothing naming the instrument.
                source_uri=f"{url}?granularity={DAILY}",
                fetched_at=utcnow(),
            )

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        provenance = Provenance(
            source_system="coinbase",
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.API,
            extractor_version=self.parser_version,
            payload_hash=payload_hash,
        )
        product = payload.source_uri.split("/products/")[-1].split("/")[0]
        subject = crypto_subject(product)

        facts: list[Fact] = []
        for candle in json.loads(payload.data):
            if not isinstance(candle, list) or len(candle) < 6:
                continue
            observed = datetime.fromtimestamp(candle[_TIME], tz=UTC).date()
            for field, index in (("PX_LAST", _CLOSE), ("PX_HIGH", _HIGH), ("PX_LOW", _LOW)):
                facts.append(
                    Fact(
                        subject=subject,
                        field=field,
                        value=float(candle[index]),
                        effective_from=observed,
                        effective_to=observed,
                        knowledge_from=payload.fetched_at,
                        provenance_id=provenance.id,
                    )
                )
        return ParsedBatch(provenance=(provenance,), facts=tuple(facts))
