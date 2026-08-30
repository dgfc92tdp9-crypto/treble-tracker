"""The IM client over a real socket, against the in-repo homeserver.

`test_matrix.py` drives `MatrixClient` by handing its calls straight into
`Homeserver.transport`. That proves the protocol and nothing about the
wire — a client and server in one process share a dict where a real
deployment shares bytes, and everything that lives in the gap (headers,
query strings, JSON encoding, status codes) is untested by construction.

So these run the same client against the same homeserver through HTTP.
What they are looking for is the class of defect the in-process tests
cannot see: a token that never reaches the server because it was put
somewhere the server does not read, a `since` cursor dropped with the
query string, a status code invented by the framework rather than by the
simulator.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from treble.im.matrix import MatrixClient
from treble.im.server import AUTHORIZATION, create_app, http_transport
from treble.im.simulator import Homeserver

USER, PASSWORD = "trader", "hunter2"


@pytest.fixture
def wired() -> tuple[MatrixClient, Homeserver, TestClient]:
    """A client whose transport is real HTTP into a real ASGI app."""
    server = Homeserver()
    server.accounts[USER] = PASSWORD
    http = TestClient(create_app(server))

    def transport(method, path, body, token):  # type: ignore[no-untyped-def]
        headers = {AUTHORIZATION: f"Bearer {token}"} if token else {}
        response = http.request(method, path, json=body, headers=headers)
        return response.status_code, response.json()

    return MatrixClient(transport=transport), server, http


class TestTheSessionSurvivesTheWire:
    def test_login_then_whoami_round_trips(
        self, wired: tuple[MatrixClient, Homeserver, TestClient]
    ) -> None:
        client, server, _ = wired
        client.login(user=USER, password=PASSWORD)
        assert client.whoami() == server.user_id(USER)

    def test_the_token_travels_in_the_authorization_header(
        self, wired: tuple[MatrixClient, Homeserver, TestClient]
    ) -> None:
        """In-process, the token is an argument and cannot be misplaced.
        Over HTTP it has to be somewhere the server reads, and the whole
        session fails silently as a 401 if it is not."""
        client, _, _ = wired
        client.login(user=USER, password=PASSWORD)
        assert client.access_token
        assert client.joined_rooms() == ()

    def test_a_wrong_password_is_a_403_from_the_simulator(
        self, wired: tuple[MatrixClient, Homeserver, TestClient]
    ) -> None:
        """The status must come from the homeserver, not from the web
        framework's own error handling."""
        client, _, _ = wired
        with pytest.raises(Exception, match=r"M_FORBIDDEN|403|Invalid password"):
            client.login(user=USER, password="wrong")  # noqa: S106

    def test_an_absent_token_is_rejected(
        self, wired: tuple[MatrixClient, Homeserver, TestClient]
    ) -> None:
        client, _, _ = wired
        with pytest.raises(Exception, match=r"M_UNKNOWN_TOKEN|401"):
            client.whoami()


class TestSendAndSync:
    def test_a_message_sent_over_http_comes_back_from_sync(
        self, wired: tuple[MatrixClient, Homeserver, TestClient]
    ) -> None:
        client, _, _ = wired
        client.login(user=USER, password=PASSWORD)
        room = client.join("!desk:treble.invalid")
        client.send(room, "morning")
        assert [event.body for event in client.sync()] == ["morning"]

    def test_the_since_cursor_survives_the_query_string(
        self, wired: tuple[MatrixClient, Homeserver, TestClient]
    ) -> None:
        """**The defect this file exists for.** `since` rides in the query
        string, and a transport that dropped it would restart every sync
        from the beginning — the client would re-receive every event it had
        already processed and report them all as new. In-process the
        argument is a string either way, so nothing there can catch it.
        """
        client, server, _ = wired
        client.login(user=USER, password=PASSWORD)
        room = client.join("!desk:treble.invalid")
        client.send(room, "first")
        assert [e.body for e in client.sync()] == ["first"]

        server.inject(room, "@other:treble.invalid", "second")
        assert [e.body for e in client.sync()] == ["second"], (
            "the second sync resumed from the cursor rather than replaying"
        )

    def test_a_retry_reuses_its_transaction_id(
        self, wired: tuple[MatrixClient, Homeserver, TestClient]
    ) -> None:
        """A resend after an ambiguous timeout must deduplicate. In a chat
        that carries a VCON this is a booked trade, not a cosmetic repeat."""
        client, _, _ = wired
        client.login(user=USER, password=PASSWORD)
        room = client.join("!desk:treble.invalid")
        first = client.send(room, "buy 100", key="order-1")
        second = client.send(room, "buy 100", key="order-1")
        assert first == second


class TestWhatTheAppDoesNotDo:
    def test_a_token_in_the_query_string_is_not_accepted(
        self, wired: tuple[MatrixClient, Homeserver, TestClient]
    ) -> None:
        """Matrix once allowed `?access_token=`, and it is deprecated
        because a token in a query string lands in every access log and
        proxy cache on the path. A client still sending it that way must
        get a 401 rather than a quiet success."""
        client, _, http = wired
        client.login(user=USER, password=PASSWORD)
        response = http.get(f"/_matrix/client/v3/account/whoami?access_token={client.access_token}")
        assert response.status_code == 401

    def test_an_unknown_path_is_the_simulators_404(
        self, wired: tuple[MatrixClient, Homeserver, TestClient]
    ) -> None:
        """Not FastAPI's. The catch-all exists so routing lives in one
        place; a route table here would be a second place for the two to
        disagree about what exists."""
        client, _, http = wired
        client.login(user=USER, password=PASSWORD)
        response = http.get(
            "/_matrix/client/v3/nonsense",
            headers={AUTHORIZATION: f"Bearer {client.access_token}"},
        )
        assert response.status_code == 404
        assert response.json()["errcode"] == "M_UNRECOGNIZED"

    def test_a_malformed_body_is_not_a_500(
        self, wired: tuple[MatrixClient, Homeserver, TestClient]
    ) -> None:
        """The client's error, answered on the homeserver's terms."""
        _, _, http = wired
        response = http.post(
            "/_matrix/client/v3/login",
            content=b"{not json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code < 500


def test_the_http_transport_has_the_same_shape_as_the_in_process_one() -> None:
    """The seam that lets one client be driven either way, and later
    against a real Synapse, without the client knowing which."""
    import inspect

    assert inspect.signature(http_transport("http://example.invalid")) == inspect.signature(
        Homeserver().transport
    )


class TestTheServedInstanceHasNoDefaultAccounts:
    """`Homeserver` carries a default account for the tests' convenience.

    That is fine in a fixture and is not fine on a socket: served as-is,
    `treble homeserver` would authenticate a user the operator never asked
    for, with a password published in the repository. The CLI clears the
    table before adding what it was told to, and this is what says so.
    """

    def test_the_fixture_default_still_exists(self) -> None:
        """Asserted so the test below is known to be testing something. If
        this ever becomes empty, the CLI's clear() is a no-op and the
        guarantee has quietly moved somewhere else."""
        assert Homeserver().accounts

    def test_the_cli_serves_only_the_accounts_it_was_given(self) -> None:
        from typer.testing import CliRunner

        from treble.cmd.cli import app

        captured: dict[str, str] = {}

        def fake_serve(server, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(server.accounts)

        import treble.im.server as im_server

        original = im_server.serve
        im_server.serve = fake_serve  # type: ignore[assignment]
        try:
            result = CliRunner().invoke(
                app, ["homeserver", "--account", "trader:s3cret", "--port", "18099"]
            )
        finally:
            im_server.serve = original  # type: ignore[assignment]

        assert result.exit_code == 0, result.output
        assert captured == {"trader": "s3cret"}, "a default account reached the socket"
