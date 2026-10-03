"""Does the vendor actually serve the symbols we claim to track?

**A hand-maintained universe goes stale, and nothing noticed.** On
2026-10-03 the 221-symbol equity list contained Discover Financial and Hess
— both acquired and delisted, one in 2024 and one in 2025 — plus Electronic
Arts, taken private. All three had been written into the config that same
day, from recollection rather than from anything checkable.

They surfaced only as failures in a populate run, and even then not
honestly: the vendor **throttles before it resolves a symbol**, so the first
run reported five 429s where four were really 404s. A retry after the limit
cleared was the only thing that could tell them apart, and the commit
message written in between said "all seventeen are rate limits" as a fact.

## One request, not two hundred

Checking coverage by asking for each symbol's history costs a request per
symbol — the same spend as the populate run it is supposed to precede, on a
tier that allows ~800 a day. The catalogue endpoint answers for every symbol
at once for a single credit, so this is a cheap thing to run often rather
than an expensive thing run once and then trusted.

## Both directions

The obvious check is "configured but not served". The other direction
matters as much and is the one a comment could never do: a symbol in
`equity_tickers_unavailable` that the vendor has *started* serving is
excluded forever by a note nobody rereads. AvalonBay and Equity Residential
are live S&P 500 REITs absent from this tier; if that changes, this is what
says so.
"""

from __future__ import annotations

import json
import os

import httpx
from pydantic import BaseModel, ConfigDict

from treble.core.universe import UniverseSpec
from treble.ingest.secrets import redact
from treble.ingest.twelvedata import API_KEY_ENV

#: The catalogue endpoint. One credit, every supported instrument.
STOCKS_URL = "https://api.twelvedata.com/stocks"

#: Narrowed to US listings. Not an optimisation — the unfiltered catalogue
#: runs to tens of thousands of instruments across every venue, and a US
#: ticker colliding with a foreign one would make "served" true for the
#: wrong instrument. `equity_tickers` is a US list; the filter says so.
COUNTRY = "United States"


class CoverageReport(BaseModel):
    """What the vendor serves, against what the universe asks for."""

    model_config = ConfigDict(frozen=True)

    #: Configured, and the vendor has no such symbol. Each one costs a
    #: request on every populate run and keeps the command's exit non-zero.
    missing: tuple[str, ...]
    #: Excluded as unavailable, but the vendor serves it now. A stale
    #: exclusion, invisible until something looks.
    returned: tuple[str, ...]
    #: Configured and served. The ordinary case.
    served: tuple[str, ...]
    #: Size of the vendor's catalogue after filtering, so a report of
    #: "everything is missing" can be told from a filter that matched
    #: nothing. Without this the two render identically.
    catalogue_size: int

    @property
    def ok(self) -> bool:
        return not self.missing and not self.returned

    def lines(self) -> tuple[str, ...]:
        out = [f"vendor catalogue: {self.catalogue_size:,} US instruments"]
        if not self.catalogue_size:
            # Loud, because every symbol looks unserved and the cause is the
            # catalogue, not the universe.
            out.append("  the catalogue came back EMPTY — treat every result below as unproven")
        out.append(f"served: {len(self.served)} of {len(self.served) + len(self.missing)}")
        for symbol in self.missing:
            out.append(f"  not served: {symbol}  (configured — move to unavailable or replace)")
        for symbol in self.returned:
            out.append(f"  now served: {symbol}  (excluded as unavailable — reconsider)")
        if self.ok:
            out.append("every configured symbol is served, and no exclusion is stale")
        return tuple(out)


def catalogue(*, client: httpx.Client | None = None) -> frozenset[str]:
    """Every US stock symbol the vendor serves, in one request.

    The key is read here rather than taken as an argument so no caller can
    log it by accident, and `redact` scrubs it from any error — `httpx`
    puts the request URL, key included, in its own exception message, which
    is how a live key reached a file on disk once already.
    """
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise RuntimeError(
            f"{API_KEY_ENV} is not set. It lives in .env, which `treble.cmd.env` parses "
            "rather than sources."
        )
    owned = client is None
    client = client or httpx.Client(timeout=60.0)
    try:
        response = client.get(STOCKS_URL, params={"country": COUNTRY, "apikey": key})
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise redact(exc) from None
    finally:
        if owned:
            client.close()
    return symbols_in(response.content)


def symbols_in(payload: bytes) -> frozenset[str]:
    """Parse the catalogue response. Separate from the fetch so it is testable.

    A response that parses to zero symbols is **not** returned as an empty
    set silently: every configured symbol would then read as unserved, and
    the report would recommend deleting a working universe. The caller sees
    the count and `lines()` says so, but the shape is checked here because
    an envelope change is the likeliest cause.
    """
    document = json.loads(payload)
    if isinstance(document, dict) and document.get("status") == "error":
        raise RuntimeError(f"vendor reported: {document.get('message', document)}")
    rows = document.get("data") if isinstance(document, dict) else document
    if not isinstance(rows, list):
        raise RuntimeError(
            f"catalogue response has no instrument list; top-level keys "
            f"{sorted(document) if isinstance(document, dict) else type(document).__name__}"
        )
    return frozenset(
        str(row["symbol"]) for row in rows if isinstance(row, dict) and row.get("symbol")
    )


def compare(spec: UniverseSpec, served: frozenset[str]) -> CoverageReport:
    """Check the universe against the catalogue, in both directions."""
    configured = tuple(spec.equity_tickers)
    return CoverageReport(
        missing=tuple(s for s in configured if s not in served),
        returned=tuple(s for s in sorted(spec.equity_tickers_unavailable) if s in served),
        served=tuple(s for s in configured if s in served),
        catalogue_size=len(served),
    )


__all__ = ["COUNTRY", "STOCKS_URL", "CoverageReport", "catalogue", "compare", "symbols_in"]
