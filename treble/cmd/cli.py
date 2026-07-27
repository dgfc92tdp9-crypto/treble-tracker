"""The `treble` command line (spec §4, §23.3 local-only mode).

Phase 1 surface: inspect and populate the security master. The screen
grammar (`IBM US Equity DES <GO>`) arrives with WP10/WP11; this is the
operational half — getting data in, and being able to see what state the
local install is in.

Every command is safe to interrupt. Population resumes from the ingest log
(I5), so Ctrl-C costs nothing beyond the step in flight.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from treble.core.universe import load_universe_config
from treble.ingest.populate import Populator
from treble.store.duck import DuckStore
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore

app = typer.Typer(
    add_completion=False,
    help="Treble Tracker — a free and open institutional finance workstation.",
)
console = Console()

DEFAULT_DATA_DIR = Path("data")
DEFAULT_CONFIG = Path("config/universe.yaml")


class ContactMissingError(Exception):
    """SEC EDGAR requires an identifying contact email on every request."""


def _contact_email(supplied: str | None) -> str:
    """EDGAR blocks unidentified requests, sometimes at the IP level
    (CLAUDE.md §6), so this fails loudly rather than defaulting."""
    email = supplied or os.environ.get("TREBLE_EDGAR_CONTACT")
    if not email or "@" not in email:
        raise ContactMissingError(
            "SEC EDGAR requires a contact email in the User-Agent. "
            "Set TREBLE_EDGAR_CONTACT=you@example.com or pass --contact."
        )
    return email


def _open_stores(data_dir: Path) -> tuple[PayloadStore, IngestLog, DuckStore]:
    data_dir.mkdir(parents=True, exist_ok=True)
    return (
        PayloadStore(data_dir / "payloads"),
        IngestLog(data_dir / "ingest.db"),
        DuckStore(data_dir / "treble.db"),
    )


def _populator(data_dir: Path, contact: str, history_days: int) -> Populator:
    payloads, log, store = _open_stores(data_dir)
    end = datetime.now(UTC).date()
    return Populator(
        payloads=payloads,
        log=log,
        store=store,
        contact_email=contact,
        fred_start=end - timedelta(days=history_days),
        fred_end=end,
        openfigi_api_key=os.environ.get("OPENFIGI_API_KEY"),
    )


@app.command()
def populate(
    universe: str = typer.Option("dev", help="Universe name from the config file."),
    data_dir: Path = typer.Option(DEFAULT_DATA_DIR, help="Where payloads and the store live."),
    config: Path = typer.Option(DEFAULT_CONFIG, help="Universe configuration file."),
    contact: str | None = typer.Option(None, help="Contact email for the EDGAR User-Agent."),
    limit: int | None = typer.Option(None, help="Stop after this many steps (for a trial run)."),
    history_days: int = typer.Option(365, help="How far back to pull macro series."),
    dry_run: bool = typer.Option(False, help="Report outstanding work without fetching."),
) -> None:
    """Populate the security master for a configured universe.

    Resumable: re-running fetches only what is missing, so an interrupted
    run loses nothing.
    """
    spec = load_universe_config(config).get(universe)
    populator = _populator(data_dir, _contact_email(contact), history_days)

    discovered: tuple[int, ...] = ()
    if spec.discovers_filers:
        # Decision 0005: the full universe resolves its filer list from
        # EDGAR at run time. ~10.4k filers, so this is announced rather
        # than silently spending minutes.
        console.print("[dim]resolving filer list from EDGAR…[/dim]")
        discovered = populator.discover_ciks()
        console.print(f"[dim]discovered {len(discovered)} filers[/dim]")

    todo = populator.outstanding(spec, discovered_ciks=discovered)
    console.print(f"[bold]{universe}[/bold]: {spec.description}")
    console.print(f"outstanding steps: {len(todo)}")
    if dry_run:
        for step in todo[:20]:
            console.print(f"  · {step}")
        if len(todo) > 20:
            console.print(f"  … and {len(todo) - 20} more")
        return
    if not todo:
        console.print("[green]Already populated — nothing to do.[/green]")
        return

    def progress(step, index: int, total: int) -> None:  # type: ignore[no-untyped-def]
        console.print(f"[dim]{index}/{total}[/dim] {step}")

    result = populator.run(spec, discovered_ciks=discovered, limit=limit, on_step=progress)
    console.print(
        f"[green]executed {result.executed}[/green], "
        f"already done {result.already_done}, "
        f"facts written {result.facts_written}"
    )
    if result.failed:
        console.print(f"[red]{len(result.failed)} step(s) failed:[/red]")
        for failed_step, error in result.failed[:10]:
            console.print(f"  · {failed_step}: {error}")
        console.print("[dim]Failed steps stay outstanding; re-run to retry.[/dim]")
        raise typer.Exit(code=1)


@app.command()
def status(
    universe: str = typer.Option("dev", help="Universe name from the config file."),
    data_dir: Path = typer.Option(DEFAULT_DATA_DIR, help="Where payloads and the store live."),
    config: Path = typer.Option(DEFAULT_CONFIG, help="Universe configuration file."),
    contact: str | None = typer.Option(None, help="Contact email for the EDGAR User-Agent."),
) -> None:
    """Show what this local install holds and what remains outstanding."""
    spec = load_universe_config(config).get(universe)
    _, log, _ = _open_stores(data_dir)
    entries = log.read()

    table = Table(title=f"Treble Tracker — {universe}")
    table.add_column("source")
    table.add_column("payloads fetched", justify="right")
    by_source: dict[str, int] = {}
    for entry in entries:
        by_source[entry.source] = by_source.get(entry.source, 0) + 1
    for source in sorted(by_source):
        table.add_row(source, str(by_source[source]))
    if not by_source:
        table.add_row("[dim]nothing ingested yet[/dim]", "0")
    console.print(table)

    if not spec.discovers_filers:
        populator = _populator(data_dir, _contact_email(contact), 365)
        console.print(f"outstanding steps: {len(populator.outstanding(spec))}")
    else:
        # Counting outstanding work for a discovery universe means a live
        # EDGAR call; say so rather than appearing to hang.
        console.print(
            "[dim]outstanding count for a discovery universe requires an EDGAR "
            "lookup; run `treble populate --dry-run` to see it.[/dim]"
        )


@app.command()
def tui(
    data_dir: Path = typer.Option(DEFAULT_DATA_DIR, help="Where payloads and the store live."),
    contact: str | None = typer.Option(None, help="Contact email for the EDGAR User-Agent."),
    theme: str = typer.Option("default", help="Colour theme: default or high-contrast."),
) -> None:
    """Open the workstation.

    Needs a populated store — run `treble populate` first, or screens will
    render honest em dashes rather than figures.
    """
    from treble.ingest.populate import fetch_company_index
    from treble.render.tui.app import run as run_tui
    from treble.render.tui.theme import get_theme
    from treble.tapi.local import LocalTapi, TickerIndex

    _, _, store = _open_stores(data_dir)
    email = _contact_email(contact)
    # Ticker resolution comes from EDGAR's own index — the same payload the
    # population runner uses for discovery, so the two cannot disagree.
    tickers = TickerIndex.from_company_index(fetch_company_index(email))
    run_tui(LocalTapi(store, tickers=tickers), theme=get_theme(theme))


@app.command()
def universes(
    config: Path = typer.Option(DEFAULT_CONFIG, help="Universe configuration file."),
) -> None:
    """List the configured universes."""
    table = Table(title="Configured universes")
    table.add_column("name")
    table.add_column("description")
    for name, spec in sorted(load_universe_config(config).universes.items()):
        table.add_row(name, spec.description)
    console.print(table)


def main() -> None:  # pragma: no cover - entry point shim
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
