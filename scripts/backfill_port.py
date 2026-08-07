"""Out-of-band backfill for PORT's factor model (spec §16).

Two legs, and the model needs both: Ken French's factor returns, and
per-name equity returns derived from Twelve Data prices. Run deliberately
rather than from a screen -- Twelve Data's free tier allows eight requests a
minute, so a universe of this size takes minutes, and a screen that blocked
on it would look broken.

The universe is chosen for cross-sectional spread rather than size alone.
Estimating SMB, HML, RMW and CMA exposures needs names that actually differ
on those dimensions; forty mega-cap technology stocks would produce four
factors with nothing to separate them and betas that mean very little.
"""

from __future__ import annotations

import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from treble.cmd.cli import DEFAULT_DATA_DIR
from treble.ingest.frenchdata import FrenchDataAdapter
from treble.ingest.twelvedata import API_KEY_ENV, TwelveDataDailyAdapter
from treble.store.duck import DuckStore
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore

UNIVERSE: tuple[str, ...] = (
    # Mega-cap technology
    "AAPL",
    "MSFT",
    "NVDA",
    "GOOGL",
    "META",
    "AVGO",
    "ORCL",
    "CRM",
    # Financials -- value and rate-sensitive
    "JPM",
    "BAC",
    "WFC",
    "GS",
    "BRK.B",
    "AXP",
    "USB",
    "SCHW",
    # Health care
    "JNJ",
    "UNH",
    "PFE",
    "MRK",
    "ABBV",
    "TMO",
    "CVS",
    # Consumer staples and discretionary
    "PG",
    "KO",
    "PEP",
    "WMT",
    "COST",
    "MCD",
    "NKE",
    "HD",
    # Industrials and energy -- the CMA and RMW spread
    "CAT",
    "GE",
    "UNP",
    "HON",
    "XOM",
    "CVX",
    "COP",
    # Utilities, telecoms, REITs -- high yield, low beta
    "NEE",
    "DUK",
    "SO",
    "VZ",
    "T",
    "AMT",
    "PLD",
)


def _load_env() -> None:
    """Parse .env. Never source it: a line without a NAME= prefix becomes a
    command whose name is the secret, which is how a key leaked once."""
    path = pathlib.Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            key, _, value = s.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    _load_env()
    if not os.environ.get(API_KEY_ENV):
        print(f"{API_KEY_ENV} is not set", file=sys.stderr)
        return 1

    data = DEFAULT_DATA_DIR
    payloads = PayloadStore(data / "payloads")
    log = IngestLog(data / "ingest.db")
    store = DuckStore(data / "treble.db")

    started = time.monotonic()
    print("=== leg 1: Ken French factors ===", flush=True)
    french = FrenchDataAdapter(payloads, log)
    facts = 0
    for batch in french.run():
        store.write_provenance(list(batch.provenance))
        store.write_facts(list(batch.facts))
        facts += len(batch.facts)
        print(f"  +{len(batch.facts):>7} facts (running {facts})", flush=True)
    print(f"factors done: {facts} facts in {time.monotonic() - started:.0f}s", flush=True)

    print(f"\n=== leg 2: {len(UNIVERSE)} equities at 8/min ===", flush=True)
    adapter = TwelveDataDailyAdapter(payloads, log, symbols=UNIVERSE)
    equity_facts = 0
    for done, batch in enumerate(adapter.run(), start=1):
        store.write_provenance(list(batch.provenance))
        store.write_facts(list(batch.facts))
        equity_facts += len(batch.facts)
        subject = batch.facts[0].subject if batch.facts else "?"
        print(
            f"  [{done:>2}/{len(UNIVERSE)}] {subject} {len(batch.facts):>6} facts "
            f"({time.monotonic() - started:.0f}s elapsed)",
            flush=True,
        )
    print(f"\nequities done: {equity_facts} facts", flush=True)
    print(f"TOTAL {facts + equity_facts} facts in {time.monotonic() - started:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
