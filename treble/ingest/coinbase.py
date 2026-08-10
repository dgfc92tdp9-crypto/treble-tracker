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
        # candles are continuous; a day without any is a dead feed.
        expected_cadence_days=1.0,
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
            source_system=self.meta.source_id,
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


PRODUCTS_URL = "https://api.exchange.coinbase.com/products/{product}"

#: The reference fields a crypto instrument needs to be more than a price
#: series, and the storage field each lands under. Kept as data so the parser
#: and the field dictionary cannot drift apart.
_PRODUCT_FIELDS: dict[str, str] = {
    "display_name": "SECURITY_NAME",
    "base_currency": "crypto:base_currency",
    "quote_currency": "crypto:quote_currency",
    "status": "crypto:status",
}

#: Increments arrive as decimal strings. Stored as numbers because they are
#: numbers: a tick size compared as text sorts "0.1" below "0.01".
_PRODUCT_NUMBERS: dict[str, str] = {
    "quote_increment": "TICK_SIZE",
    "base_increment": "crypto:base_increment",
    "min_market_funds": "crypto:min_order_value",
}


class CoinbaseProductsAdapter(SourceAdapter):
    """Instrument reference data for the configured products (spec §9.1).

    **Why this exists.** The ticker plant and the candle series both carry
    `crypto:coinbase:*` subjects, and until now the store held nothing about
    them but prices. Two consequences, and the second is the dangerous one:

    - A price with no tick size is a number whose precision nobody can
      state, and `TICK_SIZE` is what says whether 64,402.48 is a real level
      or a rounded one.
    - **A delisted product still has price history.** Without `status` and
      `trading_disabled`, an instrument Coinbase stopped trading looks
      exactly like one it still trades, and the last print looks like a
      current price rather than a final one.

    Same public endpoint family as the candle adapter, same terms.
    """

    meta = SourceMeta(
        source_id="coinbase-products",
        # product reference data changes when a pair is listed or retired.
        expected_cadence_days=None,
        description="Coinbase Exchange product reference data",
        licence="Public market data endpoints; no key, no redistribution limit stated",
        redistribution_restricted=False,
        rate_limit_per_second=3.0,
    )
    parser_version = "1"

    def __init__(
        self, payloads: PayloadStore, log: IngestLog, *, products: tuple[str, ...]
    ) -> None:
        super().__init__(payloads, log)
        if not products:
            raise ValueError("no products to describe; an empty universe fetches nothing")
        self._products = products

    def fetch(self) -> Iterator[RawPayload]:
        for product in self._products:
            self._throttle()
            url = PRODUCTS_URL.format(product=product)
            response = httpx.get(
                url,
                headers={"User-Agent": "treble-tracker"},
                timeout=60.0,
                follow_redirects=True,
            )
            response.raise_for_status()
            yield RawPayload(data=response.content, source_uri=url, fetched_at=utcnow())

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        provenance = Provenance(
            source_system=self.meta.source_id,
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.API,
            extractor_version=self.parser_version,
            payload_hash=payload_hash,
        )
        document = json.loads(payload.data)
        identifier = document.get("id")
        if not identifier:
            raise ValueError(
                "a product document with no `id` names no instrument; storing it would "
                "attach reference data to whichever subject the URI happened to suggest"
            )
        subject = crypto_subject(str(identifier))
        # Reference data is effective from when the venue was asked. It has no
        # observation date of its own — a tick size is what it is until the
        # venue changes it — so the retrieval day is the honest effective date
        # and a later fetch supersedes it bitemporally (I2).
        observed = payload.fetched_at.date()

        facts: list[Fact] = []

        def emit(field: str, value: object) -> None:
            facts.append(
                Fact(
                    subject=subject,
                    field=field,
                    value=value,
                    effective_from=observed,
                    effective_to=observed,
                    knowledge_from=payload.fetched_at,
                    provenance_id=provenance.id,
                )
            )

        for key, field in _PRODUCT_FIELDS.items():
            if (raw := document.get(key)) not in (None, ""):
                emit(field, str(raw))
        for key, field in _PRODUCT_NUMBERS.items():
            if (raw := document.get(key)) not in (None, ""):
                emit(field, float(raw))
        # Booleans explicitly rather than by absence: "not disabled" and
        # "the venue did not say" are different, and only one of them means
        # the instrument trades.
        for key, field in (("trading_disabled", "crypto:trading_disabled"),):
            if isinstance(document.get(key), bool):
                emit(field, bool(document[key]))

        if not facts:
            raise ValueError(
                f"{identifier}: no recognised reference fields in the product document; "
                "the schema has changed and guessing at it would store confident nonsense"
            )
        return ParsedBatch(provenance=(provenance,), facts=tuple(facts))
