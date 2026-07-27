"""Population runner — executes a universe plan against the adapters (WP7).

`core.universe` decides *what* a universe needs and what remains outstanding
(pure functions, no I/O). This module supplies the I/O half: it reads the
ingest log to learn what is already done, constructs the right adapter for
each step, and runs them.

It lives in `ingest/` rather than `core/` because it must import both the
store and the adapters, and `core` is the bottom architectural layer (I7).

**Every step is resumable.** Completion is derived from the ingest log, so
interrupting a run — rate limit, network drop, Ctrl-C, power loss — loses
nothing: re-running fetches only what is missing. Nothing is re-fetched
that the payload store already holds.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from treble.core.universe import (
    PopulationStep,
    UniverseSpec,
    completion_key,
    plan_steps,
    remaining_steps,
)
from treble.ingest.base import ParsedBatch, SourceAdapter
from treble.ingest.edgar import (
    COMPANYFACTS_URL,
    SUBMISSIONS_URL,
    EdgarCompanyFactsAdapter,
    EdgarSubmissionsAdapter,
)
from treble.ingest.fred import FRED_GRAPH_URL, FredAdapter
from treble.ingest.nport import ARCHIVE_URL, NportAdapter
from treble.ingest.treasury import AUCTIONS_URL, TreasuryAuctionsAdapter
from treble.store.duck import DuckStore
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore


def completed_keys(log: IngestLog) -> set[str]:
    """What the ingest log says is already fetched (I5)."""
    return {completion_key(e.source, e.source_uri) for e in log.read()}


def uri_for_step(step: PopulationStep, *, fred_start: date, fred_end: date) -> str:
    """The URI the responsible adapter will fetch for this step.

    Built from the adapters' own URL constants so there is one definition
    of each endpoint — a divergence here would silently break resumability
    by making every step look outstanding.
    """
    match step.source_id:
        case "edgar-companyfacts":
            return COMPANYFACTS_URL.format(cik=int(step.key))
        case "edgar-submissions":
            return SUBMISSIONS_URL.format(cik=int(step.key))
        case "fred":
            return (
                f"{FRED_GRAPH_URL}?id={step.key}"
                f"&cosd={fred_start.isoformat()}&coed={fred_end.isoformat()}"
            )
        case "treasury-auctions":
            return (
                f"{AUCTIONS_URL}?filter=auction_date:gte:{step.key}&page[size]=500&page[number]=1"
            )
        case "sec-nport":
            cik, accession = step.key.split("/", 1)
            return ARCHIVE_URL.format(cik=cik, accession=accession.replace("-", ""))
    raise ValueError(f"no URI mapping for source {step.source_id!r}")


class PopulationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    planned: int
    already_done: int
    executed: int
    facts_written: int
    failed: tuple[tuple[str, str], ...] = ()  # (step, error)

    @property
    def outstanding(self) -> int:
        return self.planned - self.already_done - self.executed


class Populator:
    """Runs a universe's outstanding population steps."""

    def __init__(
        self,
        *,
        payloads: PayloadStore,
        log: IngestLog,
        store: DuckStore,
        contact_email: str,
        fred_start: date,
        fred_end: date,
        openfigi_api_key: str | None = None,
    ) -> None:
        self._payloads = payloads
        self._log = log
        self._store = store
        self._contact = contact_email
        self._fred_start = fred_start
        self._fred_end = fred_end
        self._openfigi_api_key = openfigi_api_key

    def _adapter(self, step: PopulationStep) -> SourceAdapter:
        match step.source_id:
            case "edgar-companyfacts":
                return EdgarCompanyFactsAdapter(
                    self._payloads,
                    self._log,
                    ciks=(int(step.key),),
                    contact_email=self._contact,
                )
            case "edgar-submissions":
                return EdgarSubmissionsAdapter(
                    self._payloads,
                    self._log,
                    ciks=(int(step.key),),
                    contact_email=self._contact,
                )
            case "fred":
                return FredAdapter(
                    self._payloads,
                    self._log,
                    series=(step.key,),
                    start=self._fred_start,
                    end=self._fred_end,
                )
            case "treasury-auctions":
                return TreasuryAuctionsAdapter(
                    self._payloads, self._log, since=date.fromisoformat(step.key)
                )
            case "sec-nport":
                cik, accession = step.key.split("/", 1)
                return NportAdapter(
                    self._payloads,
                    self._log,
                    filings=((int(cik), accession),),
                    contact_email=self._contact,
                )
        raise ValueError(f"no adapter for source {step.source_id!r}")

    def discover_ciks(self) -> tuple[int, ...]:
        """Resolve the full filer list from EDGAR, de-duplicated and ordered.

        Ordering is deterministic (ascending CIK) so an interrupted full run
        resumes over a stable sequence rather than a reshuffled one.
        """
        payload = fetch_company_index(self._contact)
        return tuple(sorted(set(iter_discovered_ciks(payload))))

    def outstanding(
        self, spec: UniverseSpec, *, discovered_ciks: tuple[int, ...] = ()
    ) -> list[PopulationStep]:
        steps = plan_steps(spec, discovered_ciks=discovered_ciks)
        uri_for = {
            str(s): uri_for_step(s, fred_start=self._fred_start, fred_end=self._fred_end)
            for s in steps
        }
        return remaining_steps(steps, completed_keys(self._log), uri_for)

    def run(
        self,
        spec: UniverseSpec,
        *,
        discovered_ciks: tuple[int, ...] = (),
        limit: int | None = None,
        on_step: Callable[[PopulationStep, int, int], None] | None = None,
    ) -> PopulationResult:
        """Execute outstanding steps, writing facts as each completes.

        Facts are committed per step, not batched to the end: a run
        interrupted after 400 of 8,000 filers keeps those 400.
        """
        planned = plan_steps(spec, discovered_ciks=discovered_ciks)
        todo = self.outstanding(spec, discovered_ciks=discovered_ciks)
        already = len(planned) - len(todo)
        if limit is not None:
            todo = todo[:limit]

        executed = 0
        written = 0
        failed: list[tuple[str, str]] = []
        for index, step in enumerate(todo, start=1):
            if on_step is not None:
                on_step(step, index, len(todo))
            try:
                for batch in self._adapter(step).run():
                    written += self._persist(batch)
                executed += 1
            except Exception as exc:
                # abort the whole universe; the failure is reported, and the
                # step stays outstanding so a re-run retries it.
                failed.append((str(step), f"{type(exc).__name__}: {exc}"))
        return PopulationResult(
            planned=len(planned),
            already_done=already,
            executed=executed,
            facts_written=written,
            failed=tuple(failed),
        )

    def _persist(self, batch: ParsedBatch) -> int:
        self._store.write_provenance(list(batch.provenance))
        self._store.write_facts(list(batch.facts))
        return len(batch.facts)


#: EDGAR's published index of every filer with a ticker (~10.4k entries,
#: verified 2026-07-27). Content changes daily, so the filer list is
#: resolved at run time rather than enumerated in config (decision 0005).
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def fetch_company_index(contact_email: str) -> bytes:
    """Download EDGAR's company index. Network; the parse below is pure."""
    import httpx

    from treble.ingest.edgar import edgar_user_agent

    response = httpx.get(
        COMPANY_TICKERS_URL,
        headers={"User-Agent": edgar_user_agent(contact_email), "Accept-Encoding": "gzip"},
        timeout=120.0,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.content


def cached_company_index(data_dir: Path, contact_email: str, *, max_age_hours: int = 24) -> bytes:
    """EDGAR's company index, from disk when possible.

    The workstation needs this to resolve a ticker, so an uncached fetch
    made opening the application depend on EDGAR being reachable — a
    desktop app that cannot open on a train is broken. The cached copy is
    used when fresh, refreshed when stale, and fallen back to when the
    refresh fails, so the only unopenable state is "never once online".
    """
    cache = data_dir / "company_index.json"
    if cache.is_file():
        age = time.time() - cache.stat().st_mtime
        if age < max_age_hours * 3600:
            return cache.read_bytes()

    try:
        payload = fetch_company_index(contact_email)
    except Exception:
        if cache.is_file():
            # Stale beats absent: tickers change far more slowly than a day.
            return cache.read_bytes()
        raise

    data_dir.mkdir(parents=True, exist_ok=True)
    # Written via a temporary file so an interrupted write cannot leave a
    # truncated cache that would then be served as though it were valid.
    tmp = cache.with_suffix(".json.tmp")
    tmp.write_bytes(payload)
    tmp.replace(cache)
    return payload


def iter_discovered_ciks(company_tickers_payload: bytes) -> Iterator[int]:
    """CIKs from EDGAR's published company index (`company_tickers.json`).

    Pure function of the payload so the discovery path is testable offline
    and reproducible under replay (I5).
    """
    import json

    doc = json.loads(company_tickers_payload)
    rows = doc.values() if isinstance(doc, dict) else doc
    for row in rows:
        cik = row.get("cik_str") if isinstance(row, dict) else None
        if cik is not None:
            yield int(cik)
