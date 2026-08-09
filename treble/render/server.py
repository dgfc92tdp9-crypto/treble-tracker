"""Resolved screens over HTTP, for renderers in another process.

The desktop shell is a separate process, so it cannot receive a
CellBuffer by reference. This is the transport that carries one.

**It serves resolved screens, not data.** The client is handed the same
buffer the TUI renders, produced by the same resolver from the same
definition, so it cannot drift: it is never given the opportunity to
resolve anything itself. That is what makes I6 hold across a process
boundary, and it keeps I7 intact -- data reaches this module only through
TAPI, and reaches the client not at all.

It lives in the render layer rather than under ``tapi/`` because
resolving a screen is a render-layer act. The import contract caught the
original placement, where the module name read as though TAPI itself
served screens. The gRPC and Arrow Flight transports for the server
deployment path (spec 8.3) arrive at Phase 2 behind the same service
definitions.

Bound to 127.0.0.1 only. Local-only mode has no authentication because
there is no network surface; exposing this beyond loopback would need the
entitlement model in 22.1 first.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

from treble.cmd.grammar import CommandKind, parse_command
from treble.cmd.paths import DEFAULT_DATA_DIR
from treble.render.authoring import apply_layout_command
from treble.render.canvas import Canvas, resolve_canvas
from treble.render.contract.buffer import CellBuffer, layout_tree
from treble.render.contract.registry import UnknownScreenError, available, get_screen
from treble.render.contract.resolver import ScreenContext, TapiView, resolve
from treble.tapi.contribution import (
    ContributionRejectedError,
    ContributionRequest,
    ContributionService,
)

#: Loopback only — see the module docstring.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8756

#: The desktop shell's WebView runs on its own origin, so every call it
#: makes to loopback is cross-origin and needs CORS to read the response.
#: Without this the client sends requests that succeed and can never read
#: a single reply — it sat retrying `/health` against a server answering
#: 200 to each attempt.
#:
#: The list is explicit rather than "*" on purpose. Loopback is reachable
#: from any page in the user's browser, and a wildcard would let an
#: arbitrary website read this store's contents through it.
ALLOWED_ORIGINS = (
    "tauri://localhost",  # macOS and Linux WebView
    "http://tauri.localhost",  # Windows WebView2
    "https://tauri.localhost",
    "http://localhost:5173",  # `npm run tauri dev`
)


class CommandRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    line: str
    #: Point-in-time (I2). Omitted means now, applied here at the boundary
    #: rather than inside the store.
    as_of: datetime | None = None


class CommandResponse(BaseModel):
    """What the client needs to render, plus why if it cannot."""

    model_config = ConfigDict(frozen=True)

    kind: str
    status: str
    buffer: dict[str, object] | None = None
    #: Set only for `CNVS`. A canvas is many screens at once, so it cannot
    #: travel in `buffer` — and a client that fell back to rendering one of
    #: them would show a single screen where a workspace was asked for.
    canvas: list[CanvasComponentPayload] | None = None


class CanvasComponentPayload(BaseModel):
    """One component of a resolved canvas, with where it sits."""

    model_config = ConfigDict(frozen=True)

    id: str
    screen: str
    channel: str | None = None
    placement: dict[str, int] | None = None
    #: The component's layout tree. Named `tree` rather than `buffer` so this
    #: payload is the *same shape* the canvas conformance suite compares
    #: (`canvas_layout_tree`). A wire format that differed from the tested one
    #: by a field name would leave the desktop client drawing nothing while
    #: every renderer test stayed green.
    tree: dict[str, object] | None = None


class ContributionResponse(BaseModel):
    """What the network says back when a quote is accepted.

    Echoes the composites so a contributor can see immediately whether
    their level moved the market — which is the "distribution reach" the
    contribution model is paid in (spec §2.2), and the only feedback a
    participant gets that their price is live.
    """

    model_config = ConfigDict(frozen=True)

    accepted: bool
    subject: str
    contributor: str
    quoted_at: datetime
    contributors: int
    tcmp_bid: float | None = None
    tcmp_ask: float | None = None
    tgn_bid: float | None = None
    tgn_ask: float | None = None


def create_app(
    tapi: TapiView,
    *,
    contributions: ContributionService | None = None,
    canvas: Canvas | None = None,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> FastAPI:
    """Build the local TAPI service around a data path.

    `contributions` is a separate parameter rather than a widening of
    :class:`TapiView`: that protocol is the *read* path screens resolve
    through (I7), and adding a write method to it would let a resolver
    publish a quote. Supplying nothing gives an empty in-process book,
    which is this install's honest state.
    """
    contribution_service = contributions or ContributionService()
    #: No default canvas. An empty workspace and an unconfigured one look
    #: identical on screen, so `CNVS` says which rather than rendering
    #: nothing and letting the user conclude their layout was lost.
    workspace = canvas
    api = FastAPI(
        title="Treble Tracker TAPI (local)",
        version="0.1.0",
        docs_url="/api",  # backs the API function (spec §7.10)
    )

    api.add_middleware(
        CORSMiddleware,
        allow_origins=list(ALLOWED_ORIGINS),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @api.post("/contribute", response_model=ContributionResponse)
    def contribute(request: ContributionRequest) -> ContributionResponse:
        """Publish one quote to the contributed network (spec §2.2).

        The only write path this server exposes. A refusal comes back as a
        400 carrying the reason, because a contributor whose quote silently
        vanished would keep sending it — and would believe their price was
        on every reader's screen when it was not.
        """
        try:
            quote = contribution_service.contribute(request, received_at=datetime.now(UTC))
        except ContributionRejectedError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        book = contribution_service.book(request.subject, as_of=quote.quoted_at)
        tcmp_bid, tcmp_ask = book.tcmp
        tgn_bid, tgn_ask = book.tgn
        return ContributionResponse(
            accepted=True,
            subject=str(quote.subject),
            contributor=quote.contributor,
            quoted_at=quote.quoted_at,
            contributors=len(book.quotes),
            tcmp_bid=tcmp_bid,
            tcmp_ask=tcmp_ask,
            tgn_bid=tgn_bid,
            tgn_ask=tgn_ask,
        )

    @api.get("/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "screens": available()}

    @api.post("/command", response_model=CommandResponse)
    def command(request: CommandRequest) -> CommandResponse:
        """Parse and execute one command line, returning a resolved buffer.

        The client never resolves anything itself — it renders what this
        returns, which is what keeps every renderer in agreement (I6).
        """
        parsed = parse_command(request.line)
        as_of = request.as_of or datetime.now(UTC)

        if parsed.kind is CommandKind.EMPTY:
            return CommandResponse(kind=parsed.kind.value, status="")
        if parsed.kind is CommandKind.ASK:
            # §5.2: never a dead end, and honest that ASK is Phase 5.
            return CommandResponse(
                kind=parsed.kind.value,
                status=f"ASK: {parsed.ask_reason} — natural language is Phase 5.",
            )
        if parsed.kind is CommandKind.SECURITY_MENU:
            display = parsed.security.display() if parsed.security else ""
            return CommandResponse(
                kind=parsed.kind.value,
                status=f"{display}: menu navigation is not built yet; type a function.",
            )
        if parsed.function is None:
            return CommandResponse(kind=parsed.kind.value, status="no function given")

        if parsed.function == "CNVS":
            if parsed.arguments:
                nonlocal workspace
                outcome = apply_layout_command(workspace, parsed.arguments, data_dir=data_dir)
                if outcome.canvas is not None:
                    workspace = outcome.canvas
                return CommandResponse(kind=parsed.kind.value, status=outcome.status)
            return _canvas_response(workspace, tapi=tapi, as_of=as_of, kind=parsed.kind.value)

        try:
            definition = get_screen(parsed.function)
        except UnknownScreenError:
            return CommandResponse(
                kind=parsed.kind.value,
                status=f"{parsed.function}: no screen definition yet.",
            )

        try:
            buffer = resolve(
                definition,
                ScreenContext(security=parsed.security),
                as_of=as_of,
                tapi=tapi,
            )
        except Exception as exc:
            # The client gets the reason on its status line; a stack trace
            # would be neither useful nor safe to surface.
            return CommandResponse(kind=parsed.kind.value, status=f"{type(exc).__name__}: {exc}")

        stale = "  ·  contains stale values" if buffer.stale else ""
        display = parsed.security.display() if parsed.security else "global"
        return CommandResponse(
            kind=parsed.kind.value,
            status=f"{buffer.mnemonic}  ·  {display}{stale}",
            buffer=_buffer_payload(buffer),
        )

    @api.get("/screens")
    def screens() -> dict[str, list[str]]:
        return {"available": available()}

    @api.get("/screens/{mnemonic}")
    def screen(mnemonic: str) -> dict[str, object]:
        try:
            definition = get_screen(mnemonic)
        except UnknownScreenError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return definition.model_dump(mode="json")

    return api


def _canvas_response(
    workspace: Canvas | None, *, tapi: TapiView, as_of: datetime, kind: str
) -> CommandResponse:
    """Resolve the whole workspace (spec §5.3).

    Every component at one `as_of`, through the same resolver the single-
    screen path uses — a canvas must not become a second rendering path, or
    the two would eventually disagree about the same screen.
    """
    if workspace is None:
        return CommandResponse(
            kind=kind,
            status="CNVS: no canvas configured on this server. An empty workspace and an "
            "unconfigured one look the same on screen, so this says which.",
        )
    if not workspace.component_ids:
        return CommandResponse(kind=kind, status="CNVS: the canvas has no components yet.")

    try:
        buffers = resolve_canvas(workspace, tapi=tapi, as_of=as_of)
    except Exception as exc:
        return CommandResponse(kind=kind, status=f"CNVS: {type(exc).__name__}: {exc}")

    components = []
    for component_id in workspace.component_ids:
        component = workspace.component(component_id)
        components.append(
            CanvasComponentPayload(
                id=component.id,
                screen=component.screen,
                channel=component.channel.value if component.channel else None,
                placement=component.placement.model_dump() if component.placement else None,
                tree=_buffer_payload(buffers[component_id]),
            )
        )

    linked = sum(1 for c in components if c.channel)
    return CommandResponse(
        kind=kind,
        status=f"CNVS  ·  {len(components)} components, {linked} linked",
        canvas=components,
    )


def _buffer_payload(buffer: CellBuffer) -> dict[str, object]:
    """The buffer as the renderer's canonical layout tree.

    Deliberately the same JSON the conformance suite compares, so the
    desktop client is rendering the artefact its conformance is asserted
    on — not a parallel serialisation that could drift from it.
    """
    payload: dict[str, object] = json.loads(layout_tree(buffer))
    return payload


def run(
    tapi: TapiView, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
) -> None:  # pragma: no cover - process entry point
    import uvicorn

    uvicorn.run(create_app(tapi), host=host, port=port, log_level="warning")
