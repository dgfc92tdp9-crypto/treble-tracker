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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

from treble.cmd.grammar import CommandKind, parse_command
from treble.render.contract.buffer import CellBuffer, layout_tree
from treble.render.contract.registry import UnknownScreenError, available, get_screen
from treble.render.contract.resolver import ScreenContext, TapiView, resolve

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


def create_app(tapi: TapiView) -> FastAPI:
    """Build the local TAPI service around a data path."""
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
