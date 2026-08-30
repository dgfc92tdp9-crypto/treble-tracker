"""The in-repo homeserver behind a socket (P3_1).

`im/simulator.py` implements enough of the Matrix client-server API to
drive `im/matrix.py`, and until now a test handed the client's calls
straight into `Homeserver.transport`. That proved the protocol and nothing
about the wire — the same gap `ems/transport.py` was written to close for
FIX, and the same argument applies: a client and server in one process
share a dict where a real deployment shares bytes.

**Why this is worth having when Synapse exists.** It is not a replacement.
Synapse is the real homeserver and `deploy/synapse/compose.yaml` is how you
run it — except that it has never been run here, because Docker is not
installed on this machine and that dependency is an open question rather
than a settled one. So the choice was between a client verified only
against an in-process fake and one verified over HTTP against the same
fake. The second is strictly more, and it is what makes a *logged-in
session* possible at all on this install.

What it does not do is make the identity claims stronger. `im/identity.py`
proves domain control through a whoami round trip; over this server the
domain is one this process invented, so the round trip proves the transport
and nothing about who anybody is. IM says so on screen, and this module
does not change that — a homeserver you run yourself cannot verify you to
yourself.

Bound to 127.0.0.1 for the reason the render server and the FIX acceptor
are: there is no authentication here beyond the simulator's own account
table, and §22.1's entitlement model is the prerequisite for any wider
surface.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from treble.im.matrix import Transport
from treble.im.simulator import Homeserver

#: Loopback only. See the module docstring.
DEFAULT_HOST = "127.0.0.1"

#: Matrix's own default port is 8448 for federation and 8008 for the
#: client-server API on an unencrypted local listener. 8008 is the one a
#: client talks to, so it is the one used here.
DEFAULT_PORT = 8008

#: Header a Matrix client presents its access token in.
AUTHORIZATION = "authorization"
BEARER = "Bearer "


def _token(request: Request) -> str | None:
    """The access token, or None.

    Read from the Authorization header only. Matrix once allowed
    `?access_token=`, and it is deprecated precisely because a token in a
    query string lands in every access log and proxy cache between here and
    the client — so this does not accept it, and a client still sending it
    that way gets a 401 rather than a quiet success.
    """
    header = request.headers.get(AUTHORIZATION)
    if header is None or not header.startswith(BEARER):
        return None
    return header.removeprefix(BEARER) or None


def create_app(homeserver: Homeserver | None = None) -> FastAPI:
    """A FastAPI app routing real requests into the simulator.

    Deliberately one catch-all route rather than a route per endpoint.
    `Homeserver.transport` already owns the routing, the 401 on a bad
    token, and the 404 on an unknown path — restating any of it here would
    create a second place for the two to disagree, and the shape of that
    disagreement is a server that behaves differently in-process than over
    the wire, which is exactly what this exists to rule out.
    """
    server = homeserver or Homeserver()
    api = FastAPI(
        title="Treble Matrix simulator",
        summary="Enough of the Matrix client-server API to drive the IM client.",
    )

    @api.api_route("/{path:path}", methods=["GET", "POST", "PUT"])
    async def proxy(path: str, request: Request) -> JSONResponse:
        body: dict[str, Any] | None = None
        if request.method in ("POST", "PUT"):
            raw = await request.body()
            if raw:
                # A malformed body is the client's error and must not be a
                # 500: the simulator answers on its own terms for a body it
                # does not understand, so it is handed an empty one.
                try:
                    parsed = await request.json()
                except ValueError:
                    parsed = None
                body = parsed if isinstance(parsed, dict) else None

        # The full path *with* its query string: `/sync` carries `since`
        # there, and dropping it would restart every sync from the
        # beginning — a client would re-receive every event it had already
        # processed and report them as new.
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"

        status, payload = server.transport(request.method, target, body, _token(request))
        return JSONResponse(status_code=status, content=payload)

    api.state.homeserver = server
    return api


def http_transport(base_url: str, *, timeout: float = 10.0) -> Transport:
    """A `MatrixClient` transport that speaks to a real homeserver.

    The same four-argument shape `Homeserver.transport` has, so the client
    is unchanged: it is the seam that lets one client be driven in-process
    against the simulator and over a socket against this server or a real
    Synapse, without the client knowing which.

    A transport error is not turned into a status code. A refused
    connection and a 401 are different facts — one means the server is not
    there, the other that it declined — and flattening them would let an
    unreachable homeserver read as a rejected token, which is the sort of
    thing that gets debugged as a credentials problem for an afternoon.
    """

    def transport(
        method: str, path: str, body: dict[str, Any] | None, token: str | None
    ) -> tuple[int, dict[str, Any]]:
        headers = {AUTHORIZATION: f"{BEARER}{token}"} if token else {}
        response = httpx.request(
            method,
            f"{base_url.rstrip('/')}{path}",
            json=body,
            headers=headers,
            timeout=timeout,
        )
        try:
            payload = response.json()
        except ValueError:
            # A homeserver that answered with something other than JSON is
            # not one this client can reason about, and inventing an empty
            # body would present it as an empty result.
            payload = {"errcode": "M_NOT_JSON", "error": response.text[:200]}
        return response.status_code, payload if isinstance(payload, dict) else {}

    return transport


def serve(
    homeserver: Homeserver | None = None,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> None:
    """Run the simulator until interrupted."""
    import uvicorn

    uvicorn.run(create_app(homeserver), host=host, port=port, log_level="warning")


__all__ = [
    "AUTHORIZATION",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "create_app",
    "http_transport",
    "serve",
]
