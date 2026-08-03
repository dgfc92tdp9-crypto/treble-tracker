"""`POST /contribute` — the network's only write path (spec §2.2, §8.3).

The endpoint matters as much as the service behind it: a contributor whose
quote silently vanished would keep sending it, and would believe their price
was on every reader's screen when it was not. So a refusal comes back as a
400 carrying the reason, never as a 200 with nothing stored.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from treble.render.server import create_app
from treble.store.duck import DuckStore
from treble.tapi.contribution import ContributionService
from treble.tapi.local import LocalTapi

BOND = "cusip:912810UT3"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """A server with an empty book — this install's honest state."""
    app = create_app(LocalTapi(DuckStore(tmp_path / "t.db")), contributions=ContributionService())
    return TestClient(app)


def quote(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "subject": BOND,
        "contributor": "Dealer A",
        "firmness": "executable",
        "bid": 98.25,
        "ask": 98.75,
    }
    body.update(overrides)
    return body


class TestAccepted:
    def test_a_quote_is_accepted_and_echoed(self, client: TestClient) -> None:
        response = client.post("/contribute", json=quote())
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["contributor"] == "Dealer A"
        assert body["contributors"] == 1

    def test_the_composites_come_back_with_the_acknowledgement(self, client: TestClient) -> None:
        """The only feedback a participant gets that their price is live —
        and the "distribution reach" the contribution model is paid in
        (§2.2). An ack that said only "ok" would leave a contributor unable
        to tell whether their level had reached anyone."""
        client.post("/contribute", json=quote())
        body = client.post(
            "/contribute",
            json=quote(contributor="Dealer B", firmness="indicative", bid=98.40, ask=98.60),
        ).json()
        assert body["contributors"] == 2
        # TCMP is executable-only, so Dealer B's tighter indicative level
        # must not move it; TGN is every live quote, so it must.
        assert (body["tcmp_bid"], body["tcmp_ask"]) == (98.25, 98.75)
        assert (body["tgn_bid"], body["tgn_ask"]) == (98.40, 98.60)

    def test_an_arrival_timestamp_is_stamped(self, client: TestClient) -> None:
        assert client.post("/contribute", json=quote()).json()["quoted_at"]


class TestRefused:
    @pytest.mark.parametrize(
        ("body", "fragment"),
        [
            (quote(bid=101.0, ask=99.0), "crossed quote"),
            (quote(contributor="   "), "needs a contributor"),
            (quote(bid=None, ask=None), "says nothing"),
            (quote(bid=0.0), "not a price"),
            (quote(bid_size=0.0), "not a size"),
        ],
    )
    def test_a_bad_contribution_returns_400_with_the_reason(
        self, client: TestClient, body: dict[str, object], fragment: str
    ) -> None:
        response = client.post("/contribute", json=body)
        assert response.status_code == 400
        assert fragment in response.json()["detail"]

    def test_a_refused_quote_does_not_reach_the_book(self, client: TestClient) -> None:
        """The refusal must be real, not cosmetic: a 400 followed by a
        stored quote would be the worst of both."""
        client.post("/contribute", json=quote(bid=101.0, ask=99.0))
        accepted = client.post("/contribute", json=quote())
        assert accepted.json()["contributors"] == 1

    def test_a_missing_firmness_is_rejected_by_the_schema(self, client: TestClient) -> None:
        """422 rather than 400: the request never becomes a contribution at
        all, because no code path can build one that does not say whether it
        is firm."""
        body = quote()
        del body["firmness"]
        assert client.post("/contribute", json=body).status_code == 422


class TestReadPathIsUnchanged:
    def test_the_server_still_serves_screens(self, client: TestClient) -> None:
        """Adding a write path must not disturb the read path. `TapiView`
        stays read-only — a resolver that could publish a quote would break
        I7's guarantee about what a screen can do."""
        assert client.get("/health").status_code == 200
        assert "ALLQ" in client.get("/health").json()["screens"]
