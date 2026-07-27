"""Universe configuration and the resumable population plan (spec §9.4).

`config/universe.yaml` says *what* the security master covers; this module
turns that into a typed plan and, critically, decides what still needs
doing on a re-run.

**Resumability comes from the ingest log, not a side-car file.** The log is
already append-only and content-addressed (I5): if a source+payload pair is
in it, that work is done and its facts are reproducible by replay. So
"what's left?" is a query over existing state rather than bookkeeping that
can drift out of sync with reality. Interrupt a run at any point — power
loss, rate limit, Ctrl-C — and re-running resumes exactly where it stopped
without re-fetching anything already stored.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

#: Sentinel meaning "resolve the filer list from EDGAR at run time" rather
#: than from an enumerated list that would go stale immediately.
DISCOVER: Literal["discover"] = "discover"


class UniverseSpec(BaseModel):
    """One named universe from the config file."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    edgar_ciks: tuple[int, ...] | Literal["discover"]
    fred_series: tuple[str, ...] = ()
    treasury_auctions_since: date | None = None
    nport_filings: tuple[tuple[int, str], ...] = ()

    @property
    def discovers_filers(self) -> bool:
        return self.edgar_ciks == DISCOVER


class RateLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    edgar_per_second: float = 10.0
    openfigi_per_minute: float = 25.0
    gleif_per_second: float = 1.0
    treasury_per_second: float = 2.0


class UniverseConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    universes: dict[str, UniverseSpec]
    rate_limits: RateLimits = Field(default_factory=RateLimits)
    openfigi_jobs_per_request: int = 100

    def get(self, name: str) -> UniverseSpec:
        if name not in self.universes:
            available = ", ".join(sorted(self.universes))
            raise KeyError(f"unknown universe {name!r}; available: {available}")
        return self.universes[name]


def load_universe_config(path: Path) -> UniverseConfig:
    raw = yaml.safe_load(path.read_text())
    universes: dict[str, UniverseSpec] = {}
    for name, body in (raw.get("universes") or {}).items():
        ciks = body.get("edgar_ciks")
        universes[name] = UniverseSpec(
            name=name,
            description=body.get("description", ""),
            edgar_ciks=DISCOVER if ciks == DISCOVER else tuple(ciks or ()),
            fred_series=tuple(body.get("fred_series") or ()),
            treasury_auctions_since=body.get("treasury_auctions_since"),
            nport_filings=tuple(tuple(f) for f in (body.get("nport_filings") or ())),
        )
    limits = raw.get("rate_limits") or {}
    return UniverseConfig(
        universes=universes,
        rate_limits=RateLimits(**limits),
        openfigi_jobs_per_request=(raw.get("openfigi") or {}).get("jobs_per_request", 100),
    )


class PopulationStep(BaseModel):
    """One unit of population work, identified so completion is checkable."""

    model_config = ConfigDict(frozen=True)

    source_id: str  # matches SourceAdapter.meta.source_id
    key: str  # what within that source (a CIK, a series id, a dataset)

    def __str__(self) -> str:
        return f"{self.source_id}:{self.key}"


def plan_steps(
    spec: UniverseSpec, *, discovered_ciks: tuple[int, ...] = ()
) -> list[PopulationStep]:
    """Every step a full population of ``spec`` requires.

    ``discovered_ciks`` supplies the filer list when the spec says
    ``discover``; the caller fetches it, so this stays a pure function and
    is testable without network.
    """
    ciks = discovered_ciks if spec.discovers_filers else spec.edgar_ciks
    if spec.discovers_filers and not discovered_ciks:
        raise ValueError(f"universe {spec.name!r} requires discovery but no CIKs were supplied")
    steps: list[PopulationStep] = []
    for cik in ciks:
        steps.append(PopulationStep(source_id="edgar-companyfacts", key=str(cik)))
        steps.append(PopulationStep(source_id="edgar-submissions", key=str(cik)))
    for series in spec.fred_series:
        steps.append(PopulationStep(source_id="fred", key=series))
    if spec.treasury_auctions_since is not None:
        steps.append(
            PopulationStep(
                source_id="treasury-auctions",
                key=spec.treasury_auctions_since.isoformat(),
            )
        )
    for cik, accession in spec.nport_filings:
        steps.append(PopulationStep(source_id="sec-nport", key=f"{cik}/{accession}"))
    return steps


def completion_key(source_id: str, source_uri: str) -> str:
    """Identity of one completed unit of work.

    Deliberately (source, uri) rather than uri alone: the same CIK is
    fetched by two different EDGAR adapters, and completing one must not
    mark the other done.
    """
    return f"{source_id}|{source_uri}"


def remaining_steps(
    steps: list[PopulationStep], done: set[str], uri_for: dict[str, str]
) -> list[PopulationStep]:
    """Steps whose completion key is not already in ``done``.

    Pure: ``done`` is supplied by the caller (which reads the ingest log —
    core may not import store, I7). Completion is therefore derived from
    what was actually fetched, never from a side-car ledger that could
    drift out of sync with the payload store.
    """
    return [s for s in steps if completion_key(s.source_id, uri_for[str(s)]) not in done]
