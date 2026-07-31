"""Seed a fresh install so screens work before anything is ingested.

A clean checkout has an empty store, and an empty store renders every bound
cell as an em dash — indistinguishable from a company that reports nothing.
So `treble init` loads recorded payloads through the real adapters, and the
workstation opens with figures on it.

**The seed is real data, not fabricated.** These are the same recorded SEC
and Treasury payloads the test suite runs on, parsed by the same adapters,
so every seeded fact carries genuine provenance and `SPTR` traces back to an
actual filing. Nothing is invented — the alternative, plausible placeholder
numbers, is precisely the failure this system exists to prevent.

**It is a snapshot, and says so.** The facts are as current as the day the
payloads were captured, and `treble status` reports the install as seeded
until a real population runs. A user must never mistake a seeded store for
a live one.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from treble.ingest.base import RawPayload
from treble.ingest.edgar import EdgarCompanyFactsAdapter
from treble.ingest.treasury import TreasuryAuctionsAdapter
from treble.store.duck import DuckStore
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash

#: Recorded payloads live with the tests. A packaged install without them
#: simply starts empty rather than failing — an unseeded workstation is
#: usable, it just has nothing to show until `treble populate` runs.
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"

#: The capture date of these payloads. Carried into provenance so a seeded
#: fact is honestly dated rather than stamped with today.
CAPTURED_AT = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)

IBM_CIK = 51143

#: The filers the recorded payloads cover, with their real CIKs.
SEEDED_FILERS: tuple[tuple[int, str, str], ...] = (
    (IBM_CIK, "IBM", "INTERNATIONAL BUSINESS MACHINES CORP"),
)


def seed_company_index(data_dir: Path) -> int:
    """Write a company index covering exactly the seeded filers.

    Ticker resolution goes through EDGAR's company index, which is fetched
    on first use — so without this a freshly-initialised install cannot open
    its own workstation without a network. "One command to a working
    workstation" has to mean offline too.

    Not a fabrication: it maps the tickers the seed actually contains to
    their real CIKs, and nothing else. `cached_company_index` refreshes it
    from EDGAR once the install is online and the copy is a day old, so it
    heals into the full index on first real use.
    """
    cache = data_dir / "company_index.json"
    if cache.is_file():
        return 0
    index = {
        str(position): {"cik_str": cik, "ticker": ticker, "title": title}
        for position, (cik, ticker, title) in enumerate(SEEDED_FILERS)
    }
    data_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(index))
    return len(index)


def seed_available() -> bool:
    return (FIXTURES / "edgar" / f"companyfacts_CIK{IBM_CIK:010d}.json").is_file()


def seed(payloads: PayloadStore, log: IngestLog, store: DuckStore, *, contact_email: str) -> int:
    """Load the recorded payloads. Returns the number of facts written.

    Runs each payload through its real adapter rather than writing facts
    directly, so a seeded store is byte-identical to one built by ingesting
    the same bytes — which is what keeps replay (I5) meaningful on a seeded
    install.
    """
    written = 0

    companyfacts = FIXTURES / "edgar" / f"companyfacts_CIK{IBM_CIK:010d}.json"
    if companyfacts.is_file():
        data = companyfacts.read_bytes()
        adapter = EdgarCompanyFactsAdapter(
            payloads, log, ciks=(IBM_CIK,), contact_email=contact_email
        )
        written += _ingest(
            adapter,
            store,
            payloads,
            log,
            data,
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{IBM_CIK:010d}.json",
        )

    auctions = FIXTURES / "treasury" / "auctions_coupon_securities.json"
    if auctions.is_file():
        data = auctions.read_bytes()
        adapter_t = TreasuryAuctionsAdapter(payloads, log, since=date(2026, 1, 1))
        written += _ingest(
            adapter_t,
            store,
            payloads,
            log,
            data,
            "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/"
            "accounting/od/auctions_query",
        )

    return written


def _ingest(
    adapter: EdgarCompanyFactsAdapter | TreasuryAuctionsAdapter,
    store: DuckStore,
    payloads: PayloadStore,
    log: IngestLog,
    data: bytes,
    source_uri: str,
) -> int:
    """Store the payload, log it, parse it — the same order `run()` uses.

    The ordering is what I5 depends on: raw bytes are recorded before
    anything is derived from them, so a seeded install can be replayed like
    any other.
    """
    key = payloads.put(data)
    log.append(
        source=adapter.meta.source_id,
        payload_hash=key,
        source_uri=source_uri,
        fetched_at=CAPTURED_AT,
        parser_version=adapter.parser_version,
    )
    batch = adapter.parse(
        RawPayload(data=data, source_uri=source_uri, fetched_at=CAPTURED_AT),
        payload_hash(data),
    )
    store.write_provenance(list(batch.provenance))
    store.write_facts(list(batch.facts))
    return len(batch.facts)
