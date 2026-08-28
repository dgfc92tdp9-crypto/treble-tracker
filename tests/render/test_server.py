"""The local TAPI transport (spec §8.3).

The desktop client is a separate process, so these assert the wire
contract it depends on: that a command returns the same resolved buffer
the in-process renderers get, and that failures arrive as an explanation
rather than a stack trace or a dead connection.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from treble.ingest.base import RawPayload
from treble.ingest.edgar import EdgarCompanyFactsAdapter
from treble.render.contract.buffer import layout_tree
from treble.render.contract.registry import get_screen
from treble.render.contract.resolver import ScreenContext, resolve
from treble.render.server import DEFAULT_HOST, create_app
from treble.store.duck import DuckStore
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash
from treble.tapi.local import LocalTapi, TickerIndex

FIXTURES = Path(__file__).parent.parent / "fixtures"
COMPANYFACTS = FIXTURES / "edgar" / "companyfacts_CIK0000051143.json"
FETCHED = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


def an_unbuilt_mnemonic() -> str:
    """A function the grammar knows but no screen implements yet.

    Derived rather than hard-coded: this test named YAS, and silently
    started asserting the wrong thing the moment YAS was built. Anything
    still outstanding works, and the test retires itself when the last
    screen lands.
    """
    from treble.cmd.grammar import KNOWN_MNEMONICS
    from treble.render.contract.registry import available

    outstanding = sorted(set(KNOWN_MNEMONICS) - set(available()))
    if not outstanding:
        pytest.skip("every known mnemonic now has a screen definition")
    return outstanding[0]


@pytest.fixture
def tapi(tmp_path: Path) -> LocalTapi:
    store = DuckStore(tmp_path / "t.db")
    adapter = EdgarCompanyFactsAdapter(
        PayloadStore(tmp_path / "p"),
        IngestLog(tmp_path / "l.db"),
        ciks=(51143,),
        contact_email="test@example.com",
    )
    raw = RawPayload(data=COMPANYFACTS.read_bytes(), source_uri="fixture://cf", fetched_at=FETCHED)
    batch = adapter.parse(raw, payload_hash(raw.data))
    store.write_provenance(list(batch.provenance))
    store.write_facts(list(batch.facts))
    return LocalTapi(store, tickers=TickerIndex({"IBM": 51143}))


@pytest.fixture
def client(tapi: LocalTapi) -> TestClient:
    return TestClient(create_app(tapi))


class TestTransport:
    def test_health_lists_the_screens_it_can_serve(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert "DES" in response.json()["screens"]

    def test_command_returns_the_resolved_buffer(self, client: TestClient) -> None:
        response = client.post("/command", json={"line": "IBM US Equity DES"})
        assert response.status_code == 200
        buffer = response.json()["buffer"]
        assert buffer["mnemonic"] == "DES"
        assert buffer["grid"] == [22, 80]

    def test_wire_format_is_the_conformance_artefact(
        self, client: TestClient, tapi: LocalTapi
    ) -> None:
        """The buffer on the wire must be the layout tree conformance
        compares — not a parallel serialisation that could drift from it.

        If these ever diverge, the desktop client would be rendering
        something no golden has ever checked (I6)."""
        as_of = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
        response = client.post(
            "/command", json={"line": "IBM US Equity DES", "as_of": as_of.isoformat()}
        )
        served = response.json()["buffer"]

        expected = resolve(
            get_screen("DES"),
            ScreenContext(security=None),
            as_of=as_of,
            tapi=tapi,
        )
        # Resolved independently of the request, so agreement is meaningful.
        assert served["grid"] == json.loads(layout_tree(expected))["grid"]
        assert set(served) == set(json.loads(layout_tree(expected)))

    def test_as_of_is_honoured(self, client: TestClient) -> None:
        """I2: a point-in-time request must not see later knowledge."""
        before = datetime(2000, 1, 1, tzinfo=UTC)
        response = client.post(
            "/command", json={"line": "IBM US Equity DES", "as_of": before.isoformat()}
        )
        buffer = response.json()["buffer"]
        # Nothing was known in 2000, so no cell can carry provenance —
        # a resolved value and its provenance arrive together (I1), which
        # makes this the exact statement rather than a guess from the text
        # (static menu labels like "1) FA" contain digits of their own).
        assert [c for c in buffer["cells"] if c["provenance"] is not None] == []


class TestFailuresAreExplained:
    def test_unknown_ticker_explains_itself(self, client: TestClient) -> None:
        response = client.post("/command", json={"line": "ZZZZ US Equity DES"})
        assert response.status_code == 200
        assert response.json()["buffer"] is None
        assert "company index" in response.json()["status"]

    def test_natural_language_is_an_ask_not_an_error(self, client: TestClient) -> None:
        response = client.post("/command", json={"line": "what is IBM worth"})
        assert response.json()["status"].startswith("ASK:")

    def test_unbuilt_screen_says_so(self, client: TestClient) -> None:
        line = f"IBM US Equity {an_unbuilt_mnemonic()}"
        assert (
            "no screen definition yet"
            in (client.post("/command", json={"line": line}).json()["status"])
        )

    def test_empty_command_is_not_an_error(self, client: TestClient) -> None:
        response = client.post("/command", json={"line": ""})
        assert response.status_code == 200
        assert response.json()["buffer"] is None

    def test_unknown_screen_definition_is_a_404(self, client: TestClient) -> None:
        assert client.get("/screens/NOPE").status_code == 404

    def test_screen_definition_is_served(self, client: TestClient) -> None:
        assert client.get("/screens/DES").json()["mnemonic"] == "DES"

    def test_screens_index(self, client: TestClient) -> None:
        assert "DES" in client.get("/screens").json()["available"]


def test_default_bind_is_loopback_only() -> None:
    """Local-only mode has no authentication (§22.1), so the default must
    never be a routable address."""
    assert DEFAULT_HOST == "127.0.0.1"


class TestBrowserOriginAccess:
    """Regression: the desktop client sent requests the server answered
    200 to, and could not read a single response.

    Every call from the shell's WebView is cross-origin, so without CORS
    the client polls `/health` forever against a healthy server. The bug
    is invisible from the server side — which is why it is pinned here."""

    def test_desktop_origin_may_read_responses(self, client: TestClient) -> None:
        response = client.get("/health", headers={"Origin": "tauri://localhost"})
        assert response.headers.get("access-control-allow-origin") == "tauri://localhost"

    def test_desktop_origin_may_post_commands(self, client: TestClient) -> None:
        response = client.post(
            "/command",
            json={"line": "IBM US Equity DES"},
            headers={"Origin": "tauri://localhost"},
        )
        assert response.headers.get("access-control-allow-origin") == "tauri://localhost"
        assert response.json()["buffer"] is not None

    def test_preflight_is_answered(self, client: TestClient) -> None:
        response = client.options(
            "/command",
            headers={
                "Origin": "tauri://localhost",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code == 200
        assert "POST" in response.headers.get("access-control-allow-methods", "")

    def test_arbitrary_websites_are_not_granted_access(self, client: TestClient) -> None:
        """Loopback is reachable from any page the user has open, so the
        allowlist must not be a wildcard: a website must not be able to
        read this store through the local transport."""
        response = client.get("/health", headers={"Origin": "https://evil.example"})
        assert response.headers.get("access-control-allow-origin") is None

    def test_allowlist_contains_no_wildcard(self) -> None:
        from treble.render.server import ALLOWED_ORIGINS

        assert "*" not in ALLOWED_ORIGINS
