"""The `treble` command line (spec §4, §23.3 local-only mode).

Phase 1 surface: inspect and populate the security master. The screen
grammar (`IBM US Equity DES <GO>`) arrives with WP10/WP11; this is the
operational half — getting data in, and being able to see what state the
local install is in.

Every command is safe to interrupt. Population resumes from the ingest log
(I5), so Ctrl-C costs nothing beyond the step in flight.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.table import Table

from treble.cmd.env import load_env
from treble.cmd.paths import default_data_dir
from treble.cmd.seed import FIXTURES, seed, seed_available, seed_company_index
from treble.core.universe import load_universe_config
from treble.ems.simulator import Simulator
from treble.ems.transport import HOST, SimulatorServer
from treble.ingest.base import SourceAdapter
from treble.ingest.health import Freshness, overdue, source_health
from treble.ingest.populate import Populator
from treble.render.server import DEFAULT_HOST, DEFAULT_PORT
from treble.store.duck import DuckStore
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore
from treble.store.storage import maintenance_due, measure, verdict
from treble.vault.worm import Vault

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


DEFAULT_DATA_DIR = default_data_dir()
DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "universe.yaml"

#: Days of knowledge time left in the hot tier by `compact` and `storage
#: --fix`. Stated once because the two commands must agree: a `storage
#: --fix` that compacted more aggressively than `compact` would move facts
#: an in-flight ingest is still writing beside.
DEFAULT_KEEP_DAYS = 7

#: ECB daily reference rates for the majors `refresh` keeps current.
ECB_FX_SERIES = ("D.USD.EUR.SP00.A", "D.GBP.EUR.SP00.A", "D.JPY.EUR.SP00.A")


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

    # A count can only go up, so a source that stopped flowing renders
    # exactly like one that is fine. This is the part that says which.
    health = source_health(log)
    supply = Table(title="source health")
    supply.add_column("source")
    supply.add_column("state")
    # The absolute timestamp beside the relative age: "9 days" tells you
    # how bad it is, "2026-07-31" tells you what changed that day.
    supply.add_column("last fetched")
    supply.add_column("what it means")
    styles = {
        Freshness.OVERDUE: "bold red",
        Freshness.NEVER: "yellow",
        Freshness.IRREGULAR: "dim",
        Freshness.FRESH: "green",
    }
    for state in health:
        supply.add_row(
            state.source_id,
            f"[{styles[state.freshness]}]{state.freshness.value}[/]",
            "—" if state.last_fetched is None else state.last_fetched.strftime("%Y-%m-%d %H:%M"),
            state.explain(),
        )
    console.print(supply)
    stopped = overdue(health)
    if stopped:
        console.print(
            f"[bold red]{len(stopped)} source(s) have stopped flowing.[/] Anything derived "
            "from them is as old as they are, and will not say so on its own."
        )

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
def refresh(
    data_dir: Path = typer.Option(DEFAULT_DATA_DIR, help="Where payloads and the store live."),
    only: str | None = typer.Option(None, help="Refresh one source id rather than all due."),
    force: bool = typer.Option(False, help="Refresh even sources that are not yet due."),
) -> None:
    """Re-run the market feeds that have gone stale.

    The answer to "will I have to rebuild this in six months". `treble
    status` says which sources are overdue and this brings them back. A
    workstation whose data flow can only be restored by remembering which
    script to run is one that gets rebuilt instead of repaired.

    Mostly keyless, so it runs on a timer without a credential. Twelve
    Data is the exception and is included anyway, gated on its key being
    configured — it declares a one-day cadence and `status` reports it
    overdue, and a health check that nothing can ever satisfy is worse
    than no health check, because it teaches the reader to ignore the
    column. Its 45 tickers had been fetched exactly once, by hand, on
    2026-08-07: nothing in the package constructed the adapter at all.

    Deliberately not everything. The EDGAR and N-PORT adapters are driven
    by a universe — which filers, over what history — and belong to
    `populate`, whose whole job is deciding that. Refresh is for the feeds
    that simply have a newest value.
    """
    from treble.ingest.coinbase import CoinbaseCandlesAdapter
    from treble.ingest.dtcc import DtccSdrRatesAdapter
    from treble.ingest.ecb import EcbExchangeRatesAdapter
    from treble.ingest.ecb_hicp import EcbHicpAdapter
    from treble.ingest.fred import FredAdapter
    from treble.ingest.gleif_isin import GleifIsinLeiAdapter
    from treble.ingest.health import Freshness, source_health
    from treble.ingest.treasury import TreasuryAuctionsAdapter
    from treble.ingest.treasury_curve import TreasuryCurveAdapter
    from treble.ingest.twelvedata import API_KEY_ENV, TwelveDataDailyAdapter

    payloads, log, store = _open_stores(data_dir)
    now = datetime.now(UTC)

    def _existing(prefix: str, *, drop: int) -> tuple[str, ...]:
        """What this store already tracks under a namespace.

        Refresh keeps existing series current; `populate` decides which
        series a universe wants. Reading the answer from the store rather
        than a config file is the difference between a workstation you
        repair and one you rebuild: nobody has to remember which FRED
        series they set up eighteen months ago, because the store knows.
        """
        subjects = store.subjects_with_prefix(prefix, as_of=now)
        return tuple(sorted({str(s).split(":", drop)[drop] for s in subjects}))

    fred_series = _existing("fred:", drop=1)
    stored_isins = tuple(str(s) for s in store.subjects_with_prefix("isin:", as_of=now))
    crypto_products = _existing("crypto:coinbase:", drop=2)
    equity_tickers = _existing("equity:", drop=1)

    builders: dict[str, Callable[[], SourceAdapter]] = {
        "treasury-curve": lambda: TreasuryCurveAdapter(payloads, log),
        # Auctions run on a weekly cycle, so a window rather than a day.
        # 180 back because the cost of overlap is a content-addressed
        # re-fetch that writes nothing (I5), and the cost of too narrow a
        # window is a permanent hole after any period the command was not
        # run — which is exactly the state this source was found in,
        # 27 days stale with nothing able to refresh it.
        "treasury-auctions": lambda: TreasuryAuctionsAdapter(
            payloads, log, since=now.date() - timedelta(days=180)
        ),
        # The three majors the store's fx: subjects are built from. Named
        # here rather than read from the universe config because refresh is
        # about keeping existing series current, not about choosing which
        # series a universe wants — that decision belongs to `populate`.
        "ecb-fx": lambda: EcbExchangeRatesAdapter(payloads, log, series=ECB_FX_SERIES),
        "ecb-hicp": lambda: EcbHicpAdapter(payloads, log),
        # Both of these refresh whatever the store already holds. A window
        # of a year rather than a day: FRED restates, and re-reading a span
        # that overlaps what is stored is how a revision arrives at all —
        # bitemporality (I2) keeps the original alongside it rather than
        # overwriting, so the cost of overlap is disk, and the cost of no
        # overlap is silently missing every correction.
        "fred": lambda: FredAdapter(
            payloads,
            log,
            series=fred_series,
            start=now.date() - timedelta(days=365),
            end=now.date(),
        ),
        "coinbase": lambda: CoinbaseCandlesAdapter(payloads, log, products=crypto_products),
        # Scoped to the bonds this store holds, not the whole 9.1M-row
        # file: the mapping exists to identify *our* instruments' issuers,
        # and ingesting ten million rows to answer for two thousand bonds
        # would be paying GLEIF's scale for our question.
        "gleif-isin": lambda: GleifIsinLeiAdapter(
            payloads,
            log,
            isins=[i.split(":", 1)[1] for i in stored_isins],
        ),
        # The tickers the store already holds, like FRED's series and
        # Coinbase's products above — refresh keeps existing series
        # current; choosing new ones belongs to `populate`.
        "twelvedata": lambda: TwelveDataDailyAdapter(payloads, log, symbols=equity_tickers),
        # The public CFTC Part 43 tape, by report date. Ten days rather
        # than one: the tape is published per trading day and a run that
        # asked only for today would leave a permanent hole after any
        # weekend the command was not run over. Days already ingested cost
        # a fetch and write nothing new — payloads are content-addressed
        # (I5), so a repeat is an idempotent no-op rather than a duplicate.
        "dtcc-sdr": lambda: DtccSdrRatesAdapter(
            payloads,
            log,
            report_dates=tuple(now.date() - timedelta(days=n) for n in range(1, 11)),
        ),
    }
    # A source with nothing to refresh is skipped rather than run empty: an
    # adapter asked for zero series fetches nothing and logs a success,
    # which would mark it fresh and hide that it holds no data at all.
    # Keyed sources are skipped, not failed, when the credential is absent:
    # a missing optional key is a configuration choice, and reporting it as
    # a broken feed would put a permanent red line in `status` for a source
    # the user has decided not to use.
    if not os.environ.get(API_KEY_ENV):
        builders.pop("twelvedata", None)

    empty = {
        source_id
        for source_id, targets in (
            ("fred", fred_series),
            ("twelvedata", equity_tickers),
            ("coinbase", crypto_products),
            ("gleif-isin", stored_isins),
        )
        if not targets
    }
    for source_id in sorted(empty):
        builders.pop(source_id, None)
    if only is not None and only not in builders:
        console.print(f"[red]{only}[/] is not refreshable here; try: {', '.join(builders)}")
        raise typer.Exit(1)

    due = {
        state.source_id
        for state in source_health(log)
        if state.freshness in {Freshness.OVERDUE, Freshness.NEVER}
    }
    targets = [only] if only else sorted(builders)
    ran = 0
    for source_id in targets:
        if not force and only is None and source_id not in due:
            console.print(f"[dim]{source_id}: already fresh, skipped[/dim]")
            continue
        try:
            written = 0
            for batch in builders[source_id]().run():
                store.write_provenance(list(batch.provenance))
                store.write_facts(list(batch.facts))
                written += len(batch.facts)
        except Exception as exc:
            # Reported and carried past, because the common case for this
            # command is exactly that one source has broken. Aborting would
            # mean a single dead endpoint blocks every healthy one.
            console.print(f"[red]{source_id}: {type(exc).__name__}: {exc}[/]")
            continue
        ran += 1
        console.print(f"[green]{source_id}[/]: {written} facts")
    console.print(f"refreshed {ran} of {len(targets)} source(s); run `treble status` to confirm")
    _maintain(store)


def _maintain(store: DuckStore) -> None:
    """Compact after an ingest, if the hot tier has grown enough to warrant it.

    The store reached 1.2 GB because `compact` was correct, tested, 21x
    effective and *manual*. Nothing ran it, so every ingest added to a hot
    tier that never shrank. This is the smallest change that makes that
    impossible: the command that grows the store is the one that cleans up
    after it.

    Failures here are reported and swallowed. Compaction is crash-safe by
    construction — it writes a temporary file, verifies it by hash, and
    only then renames and deletes — so the worst case is that the store
    stays large, which is the situation this improves rather than one it
    creates. Aborting a successful `refresh` because the optional tidy-up
    failed would lose the ingest that did work.
    """
    hot = store.hot_fact_count()
    if not maintenance_due(hot):
        return
    console.print(f"[dim]hot tier at {hot:,} facts — compacting…[/dim]")
    try:
        report = store.compact(before=datetime.now(UTC) - timedelta(days=DEFAULT_KEEP_DAYS))
    except Exception as exc:
        console.print(f"[yellow]compaction skipped: {type(exc).__name__}: {exc}[/yellow]")
        return
    if report.moved_anything:
        console.print(f"[dim]moved {report.rows_moved:,} settled facts to the cold tier[/dim]")


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


@app.command()
def compact(
    data_dir: Path = typer.Option(DEFAULT_DATA_DIR, help="Where the store lives."),
    keep_days: int = typer.Option(
        DEFAULT_KEEP_DAYS, help="Days of knowledge time to leave in the hot tier."
    ),
    namespace: list[str] = typer.Option(
        [], help="Only compact these namespaces. Default: all with settled facts."
    ),
    reclaim: bool = typer.Option(
        True, help="Rebuild the database file so the freed space returns to the disk."
    ),
) -> None:
    """Move settled facts into the cold Parquet tier.

    Measured 19x smaller than the hot tier for the same rows, with reads
    within noise of DuckDB native. Safe to interrupt: nothing is deleted
    until the replacement has been written and read back.
    """
    store = DuckStore(data_dir / "treble.db")
    before = datetime.now(UTC) - timedelta(days=keep_days)
    before_bytes = (data_dir / "treble.db").stat().st_size

    console.print(f"Compacting facts known before {before:%Y-%m-%d %H:%M} UTC")
    report = store.compact(before=before, namespaces=tuple(namespace) or None)

    if not report.moved_anything:
        console.print("[yellow]Nothing settled to compact.[/yellow]")
        return

    table = Table(title="Compacted")
    table.add_column("namespace")
    table.add_column("moved", justify="right")
    table.add_column("cold rows", justify="right")
    table.add_column("cold size", justify="right")
    table.add_column("bytes/row", justify="right")
    for result in sorted(report.results, key=lambda r: -r.rows_moved):
        table.add_row(
            result.namespace,
            f"{result.rows_moved:,}",
            f"{result.cold_rows:,}",
            f"{result.cold_bytes / 1e6:,.1f} MB",
            f"{result.bytes_per_row:.1f}",
        )
    console.print(table)

    # `compact` alone leaves the file the size it was — DuckDB frees the
    # blocks internally and never returns them to the filesystem. Without
    # this step the command would truthfully report a gigabyte moved and
    # the user would see no change on disk at all.
    if reclaim:
        console.print("[dim]rebuilding the database file to release freed space…[/dim]")
        store.reclaim()
    after_bytes = (data_dir / "treble.db").stat().st_size
    console.print(
        f"hot tier {before_bytes / 1e6:,.0f} MB -> {after_bytes / 1e6:,.0f} MB, "
        f"cold tier {report.cold_bytes / 1e6:,.0f} MB "
        f"({report.rows_moved:,} facts moved)"
    )
    if not reclaim:
        console.print(
            "[yellow]--no-reclaim: the file keeps its old size until you rebuild it.[/yellow]"
        )

    conflicts = store.ambiguous_partitions(limit=5)
    if conflicts:
        console.print(
            "\n[yellow]Keys holding more than one value, of which the screens show "
            "one (mostly multi-valued fields modelled as single-valued):[/yellow]"
        )
        for subject, field, effective, count in conflicts:
            console.print(f"  · {subject} {field} eff {effective}: {count} values")
    if report.skipped:
        # Named rather than counted: a namespace silently staying hot for
        # ever is the kind of thing that gets noticed as a disk-space
        # mystery months later.
        console.print(
            f"[yellow]Left hot — namespace not a safe file name: "
            f"{', '.join(report.skipped)}[/yellow]"
        )


@app.command()
def storage(
    data_dir: Path = typer.Option(DEFAULT_DATA_DIR, help="Where the store lives."),
    fix: bool = typer.Option(False, help="Reclaim what can be reclaimed losslessly."),
) -> None:
    """Report what the data directory is using, and what of it is waste.

    Read-only unless `--fix` is passed. `waste` means bytes a documented,
    lossless command would return — never source data: `payloads/` holds
    the content-addressed bytes every fact was derived from, and no
    quantity of them is ever reported as reclaimable.

    The same numbers back `make gate`, so a store that passes here passes
    there.
    """
    report = measure(data_dir)
    if not report.components:
        console.print(f"[yellow]Nothing at {data_dir}.[/yellow]")
        return

    table = Table(title=f"{data_dir}")
    table.add_column("component")
    table.add_column("size", justify="right")
    table.add_column("reclaimable", justify="right")
    table.add_column("remedy")
    for component in sorted(report.components, key=lambda c: -c.size):
        table.add_row(
            component.name,
            f"{component.size / 1024 / 1024:,.1f} MB",
            f"{component.waste / 1024 / 1024:,.1f} MB" if component.waste else "—",
            component.remedy or "",
        )
    console.print(table)

    result = verdict(report)
    style = "green" if result.ok else "red"
    console.print(
        f"[{style}]{report.size / 1024 / 1024:,.1f} MB total, {result.summary}.[/{style}]"
    )
    if not fix:
        if not result.ok:
            console.print("[yellow]Run `treble storage --fix` to reclaim it.[/yellow]")
        return

    # Compaction first: it is the lossless one, verified by hash before
    # anything is deleted. Backups are removed only afterwards, and only
    # once the live store has answered a query — deleting a copy before
    # confirming the original reads is the one ordering that can lose data.
    store = DuckStore(data_dir / "treble.db")
    before = datetime.now(UTC) - timedelta(days=DEFAULT_KEEP_DAYS)
    compaction = store.compact(before=before)
    if compaction.moved_anything:
        console.print(f"compacted {compaction.rows_moved:,} facts into the cold tier")
    store.reclaim()

    facts = store.fact_count()
    console.print(f"[green]store verified: {facts:,} facts readable after reclaim[/green]")

    removed = 0
    for component in measure(data_dir).wasteful:
        if component.path.is_file() and component.path.name != "treble.db":
            size = component.path.stat().st_size
            component.path.unlink()
            removed += size
            console.print(f"removed {component.name} ({size / 1024 / 1024:,.1f} MB)")
    console.print(
        f"[green]reclaimed {(report.waste) / 1024 / 1024:,.1f} MB "
        f"({removed / 1024 / 1024:,.1f} MB of it hand-made copies)[/green]"
    )


@app.command()
def simulator(
    port: int = typer.Option(0, help="Port to bind. 0 picks a free one and prints it."),
    heartbeat: float = typer.Option(30.0, help="HeartBtInt in seconds. 0 disables the clock."),
    fill_slices: int = typer.Option(1, help="Execution reports per immediate fill."),
    rest: bool = typer.Option(False, help="Rest orders instead of filling them."),
    state_dir: Path | None = typer.Option(None, help="Persist sequence counters here."),
    archive: bool = typer.Option(False, help="Retain every message under the TVault schedule."),
    data_dir: Path = typer.Option(DEFAULT_DATA_DIR, help="Where the vault lives, with --archive."),
) -> None:
    """Run the FIX 4.4 acceptor so a client can be pointed at something.

    Bound to 127.0.0.1 and not configurable. There is no authentication
    in this session layer, so an acceptor reachable from a network is one
    anybody on that network can send orders to.

    It is a simulator and says so: it fills at the order's limit price and
    invents nothing about the market. What it is for is the session — the
    counters, the framing, the refusals — which is where an EMS loses
    money quietly.
    """
    sim = Simulator(fill_immediately=not rest, fill_slices=fill_slices)
    vault = Vault(data_dir / "vault") if archive else None
    if state_dir is not None:
        state_dir.mkdir(parents=True, exist_ok=True)

    async def serve() -> None:
        server = SimulatorServer(sim, state_dir=state_dir, vault=vault, heartbeat_seconds=heartbeat)
        bound = await server.start(port=port)
        console.print(f"FIX 4.4 acceptor on [bold]{HOST}:{bound}[/] as {sim.sender}->{sim.target}")
        console.print(
            f"[dim]heartbeat {heartbeat:g}s · "
            f"{'resting' if rest else f'filling in {fill_slices}'} · "
            f"counters {'persisted' if state_dir else 'in memory'} · "
            f"{'archiving' if vault else 'not archived'}[/dim]"
        )
        console.print("[dim]Ctrl-C to stop.[/dim]")
        try:
            await asyncio.Event().wait()
        finally:
            await server.stop()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        # Expected: this command runs until interrupted. A traceback here
        # would make the ordinary way of stopping it look like a crash.
        console.print(f"\nStopped. {sim.reports} execution report(s), {sim.fills} fill(s).")


def main() -> None:  # pragma: no cover - entry point shim
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
