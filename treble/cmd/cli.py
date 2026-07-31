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
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.table import Table

from treble.cmd.env import load_env
from treble.cmd.seed import FIXTURES, seed, seed_available, seed_company_index
from treble.core.universe import load_universe_config
from treble.ingest.populate import Populator
from treble.render.server import DEFAULT_HOST, DEFAULT_PORT
from treble.store.duck import DuckStore
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore

if TYPE_CHECKING:
    from treble.tapi.local import LocalTapi

app = typer.Typer(
    add_completion=False,
    help="Treble Tracker — a free and open institutional finance workstation.",
)

# Credentials live in a gitignored .env. Loading here means every command
# sees them; without it the file exists and is silently ignored, which is
# precisely how `treble tui` came to refuse valid credentials.
load_env()
console = Console()


def _default_data_dir() -> Path:
    """Where the workstation looks for its store, independent of cwd.

    This was a relative ``Path("data")``, so which store opened depended on
    the directory the command ran from. Launched from the Dock, or from a
    terminal anywhere but the repo root, it silently created a fresh empty
    store and rendered a screen of honest-looking dashes with no error —
    the exact shape of a wrong display that never announces itself.

    ``TREBLE_DATA_DIR`` overrides. Otherwise the repo's own ``data/`` is
    used when this is a source checkout, falling back to ``~/.treble`` for
    an installed copy that has no repo to anchor to.
    """
    override = os.environ.get("TREBLE_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    repo_root = Path(__file__).resolve().parents[2]
    if (repo_root / "pyproject.toml").is_file():
        return repo_root / "data"
    return Path.home() / ".treble"


DEFAULT_DATA_DIR = _default_data_dir()
DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "universe.yaml"


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
def init(
    data_dir: Path = typer.Option(DEFAULT_DATA_DIR, help="Where payloads and the store live."),
    contact: str | None = typer.Option(None, help="Contact email for the EDGAR User-Agent."),
) -> None:
    """Set up a fresh install: create the store, seed it, and say what is next.

    One command from a clean checkout to a workstation with figures on it.
    A store with no data renders every bound cell as an em dash, which is
    indistinguishable from a company that reports nothing — so a new install
    is seeded with recorded SEC and Treasury payloads rather than left empty.

    Idempotent: running it on a populated install reports what is there and
    changes nothing.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    payloads, log, store = _open_stores(data_dir)

    existing = store.fact_count()
    if existing:
        console.print(f"Already set up: {existing:,} facts in {data_dir}.")
        console.print("Run [bold]treble tui[/] to open the workstation.")
        return

    try:
        email = _contact_email(contact)
    except ContactMissingError as missing:
        # EDGAR requires an identifying contact on every request, so this is
        # needed before any real population — but the seed does not touch
        # the network, so the install is still usable now.
        console.print(f"[bold yellow]note[/]: {missing}")
        console.print("Seeding anyway; set it before running `treble populate`.")
        email = "unset@example.invalid"

    if not seed_available():
        console.print(
            f"[bold yellow]warning[/]: no recorded payloads found at {FIXTURES}. "
            "The store is empty, so screens will render dashes until "
            "`treble populate` runs."
        )
        return

    written = seed(payloads, log, store, contact_email=email)
    tickers = seed_company_index(data_dir)
    console.print(f"Seeded {written:,} facts from recorded SEC and Treasury payloads.")
    if tickers:
        console.print(f"Seeded a {tickers}-ticker company index, so the workstation opens offline.")
    console.print(
        "[dim]A point-in-time snapshot, not live data — run "
        "`treble populate` for current figures.[/dim]"
    )
    console.print()
    console.print("Try:  [bold]treble tui[/]  then type  [bold]IBM US Equity DES[/]")


@app.command()
def addin() -> None:
    """How to load the spreadsheet functions into Excel (spec §4.1)."""
    from treble.addin import udf

    console.print("[bold]Treble Tracker spreadsheet functions[/]")
    console.print()
    console.print('  =TDP("IBM US Equity", "us-gaap:Assets:USD")')
    console.print('  =TDH("SP500 Index", "PX_LAST", "1/1/2026", "12/31/2026")')
    console.print('  =TDS("SPX Index", "INDX_MWEIGHT_HIST")')
    console.print("  =TQL(\"get(int_rate) for(bonds(security_type='Bond'))\")")
    console.print()
    console.print("To install, once:")
    console.print("  [bold]uv run xlwings addin install[/]")
    console.print("Then in Excel, set the UDF Modules field to:")
    console.print(f"  [bold]{Path(udf.__file__).with_suffix('').as_posix()}[/]")
    console.print()
    console.print(
        "[dim]No rate limit and no redistribution restriction on this surface "
        "(§4.1): the data is public, and there is nothing to meter.[/dim]"
    )


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
    from treble.render.tui.app import run as run_tui
    from treble.render.tui.theme import get_theme

    run_tui(_local_tapi(data_dir, contact), theme=get_theme(theme))


def _local_tapi(data_dir: Path, contact: str | None) -> LocalTapi:
    """The data path both clients open on.

    Shared deliberately: the desktop shell and the TUI must resolve the same
    ticker to the same security from the same store, and the only way to
    guarantee that is for there to be one place it is decided.
    """
    from treble.ingest.populate import cached_company_index
    from treble.tapi.local import LocalTapi, TickerIndex

    _, _, store = _open_stores(data_dir)
    # Ticker resolution comes from EDGAR's own index — the same payload the
    # population runner uses for discovery, so the two cannot disagree.
    if store.fact_count() == 0:
        # Silence here would be indistinguishable from a company that
        # reports nothing: every bound cell renders as a dash either way.
        console.print(
            f"[bold yellow]warning[/]: no facts in {data_dir} — screens will render dashes. "
            "Run `treble populate` first."
        )
    index = cached_company_index(data_dir, _contact_email(contact))
    return LocalTapi(store, tickers=TickerIndex.from_company_index(index))


@app.command()
def serve(
    data_dir: Path = typer.Option(DEFAULT_DATA_DIR, help="Where payloads and the store live."),
    contact: str | None = typer.Option(None, help="Contact email for the EDGAR User-Agent."),
    host: str = typer.Option(DEFAULT_HOST, help="Bind address. Loopback by default."),
    port: int = typer.Option(DEFAULT_PORT, help="Port for the local TAPI transport."),
) -> None:
    """Serve TAPI over HTTP for the desktop client.

    The desktop shell is a separate process, so it reaches the same data
    path over loopback rather than in-process. It receives resolved
    CellBuffers, never raw data — which is what keeps every renderer in
    agreement (I6).
    """
    from treble.render.server import run as run_server

    if host != DEFAULT_HOST:
        # §22.1's entitlement model does not exist yet, so anything beyond
        # loopback would be an unauthenticated view of the whole store.
        console.print(
            f"[bold red]refusing to bind {host}[/]: local-only mode has no authentication; "
            "only 127.0.0.1 is served until the entitlement model lands (spec §22.1)."
        )
        raise typer.Exit(code=2)

    console.print(f"TAPI on http://{host}:{port}  ·  docs at /api")
    run_server(_local_tapi(data_dir, contact), host=host, port=port)


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
