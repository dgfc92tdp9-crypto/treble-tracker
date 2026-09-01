"""A throttled GET that survives one bad response — and refuses the wrong ones.

`twelvedata` failed two full refreshes in a row on 2026-09-01 with
``RemoteProtocolError: peer closed connection without sending complete
message body``. It fetches 45 symbols at eight requests a minute — six
unbroken minutes of calls — and a single truncated response ended the
source. Fifteen payloads had already been stored; the sixteenth killed
the run.

The tests that matter here are the ones about what is **not** retried. A
retry that repeats a bad key three times turns a clear error into a slow
one and spends the quota that would have fixed it.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from treble.ingest import base as base_module
from treble.ingest.base import (
    MAX_ATTEMPTS,
    RETRIABLE_STATUS,
    ParsedBatch,
    RawPayload,
    SourceAdapter,
    SourceMeta,
)
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore

URL = "https://example.invalid/series"


class Probe(SourceAdapter):
    meta = SourceMeta(
        source_id="probe",
        description="d",
        licence="l",
        # No throttle: these tests are about retries, and a token bucket
        # would make each of them take seconds for nothing.
        rate_limit_per_second=None,
    )
    parser_version = "1"

    def fetch(self):  # pragma: no cover - not the subject
        raise NotImplementedError

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        raise NotImplementedError  # pragma: no cover - not the subject


@pytest.fixture
def probe(tmp_path: Path) -> Probe:
    return Probe(PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backoff is real seconds. Tested separately, not waited through."""
    monkeypatch.setattr(base_module.time, "sleep", lambda _: None)


class Responder:
    """Serves a scripted sequence, remembering how many calls it took."""

    def __init__(self, *outcomes: object) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, url: str, *, params=None, timeout=None) -> httpx.Response:
        self.calls += 1
        outcome = self._outcomes[min(self.calls - 1, len(self._outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(int(outcome), request=httpx.Request("GET", url))


def _serve(monkeypatch: pytest.MonkeyPatch, responder: Responder) -> Responder:
    monkeypatch.setattr(base_module.httpx, "get", responder)
    return responder


TRUNCATED = httpx.RemoteProtocolError(
    "peer closed connection without sending complete message body"
)


class TestATransientFailureIsRetried:
    def test_a_truncated_response_is_retried_and_succeeds(
        self, probe: Probe, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exact failure, and the exact recovery."""
        responder = _serve(monkeypatch, Responder(TRUNCATED, 200))
        assert probe._get(URL).status_code == 200
        assert responder.calls == 2

    def test_a_read_timeout_is_retried(self, probe: Probe, monkeypatch: pytest.MonkeyPatch) -> None:
        responder = _serve(monkeypatch, Responder(httpx.ReadTimeout("slow"), 200))
        assert probe._get(URL).status_code == 200
        assert responder.calls == 2

    @pytest.mark.parametrize("status", sorted(RETRIABLE_STATUS))
    def test_rate_limits_and_server_errors_are_retried(
        self, probe: Probe, monkeypatch: pytest.MonkeyPatch, status: int
    ) -> None:
        responder = _serve(monkeypatch, Responder(status, 200))
        assert probe._get(URL).status_code == 200
        assert responder.calls == 2

    def test_it_gives_up_and_re_raises_the_vendor_error(
        self, probe: Probe, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-raised rather than wrapped, so the caller still sees what the
        vendor actually said."""
        responder = _serve(monkeypatch, Responder(TRUNCATED))
        with pytest.raises(httpx.RemoteProtocolError, match="peer closed"):
            probe._get(URL)
        assert responder.calls == MAX_ATTEMPTS


class TestWhatIsNotRetried:
    """The half that keeps a retry from making things worse."""

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_a_client_error_is_raised_immediately(
        self, probe: Probe, monkeypatch: pytest.MonkeyPatch, status: int
    ) -> None:
        """A bad key, an unknown symbol, a withdrawn tier. Repeating it
        three times turns a clear error into a slow one while spending the
        quota that would have fixed it."""
        responder = _serve(monkeypatch, Responder(status))
        with pytest.raises(httpx.HTTPStatusError):
            probe._get(URL)
        assert responder.calls == 1

    def test_429_is_not_treated_as_a_client_error(self) -> None:
        """It is a 4xx, and it is the one that means "wait", not "wrong"."""
        assert 429 in RETRIABLE_STATUS


class TestTheCredentialIsNotLeaked:
    def test_params_do_not_reach_the_raised_error(
        self, probe: Probe, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Several adapters put an API key in `params`. A retry that named
        its arguments in an exception would put the key into every log and
        traceback that error reaches."""
        _serve(monkeypatch, Responder(TRUNCATED))
        with pytest.raises(httpx.RemoteProtocolError) as caught:
            probe._get(URL, params={"apikey": "SECRET-VALUE", "symbol": "IBM"})
        assert "SECRET-VALUE" not in str(caught.value)

    def test_the_key_is_still_sent(self, probe: Probe, monkeypatch: pytest.MonkeyPatch) -> None:
        """Proves the assertion above is not passing because params were
        quietly dropped."""
        seen: list[object] = []

        def capture(url: str, *, params=None, timeout=None) -> httpx.Response:
            seen.append(params)
            return httpx.Response(200, request=httpx.Request("GET", url))

        monkeypatch.setattr(base_module.httpx, "get", capture)
        probe._get(URL, params={"apikey": "SECRET-VALUE"})
        assert seen == [{"apikey": "SECRET-VALUE"}]


class TestThrottling:
    def test_every_attempt_takes_a_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A retry that skipped the bucket would burst straight past the
        rate limit that caused the 429 it is retrying."""

        class Throttled(Probe):
            meta = SourceMeta(
                source_id="throttled",
                description="d",
                licence="l",
                rate_limit_per_second=1000.0,
            )

        adapter = Throttled(PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"))
        taken = 0

        original = adapter._bucket.acquire  # type: ignore[union-attr]

        def counting() -> None:
            nonlocal taken
            taken += 1
            original()

        monkeypatch.setattr(adapter._bucket, "acquire", counting)  # type: ignore[union-attr]
        _serve(monkeypatch, Responder(TRUNCATED, TRUNCATED, 200))
        adapter._get(URL)
        assert taken == 3


class TestTheBoundaryOfWhatCountsAsTransient:
    """Which exception class is caught, pinned.

    Found by mutation: widening `except httpx.TransportError` to its parent
    `httpx.HTTPError` killed no test. Status errors are caught by the clause
    above it, so the only behaviour that changed was for `RequestError`
    subclasses no test exercised — a real gap at the boundary rather than a
    wrong answer.

    The line is: **retry only when nothing was observed.** A connection
    reset or a truncated body means no response arrived, so asking again
    can produce a different outcome. A response that arrived and was then
    found wrong will be equally wrong the second time.
    """

    def test_a_body_that_arrived_and_would_not_decode_is_not_retried(
        self, probe: Probe, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        responder = _serve(monkeypatch, Responder(httpx.DecodingError("bad gzip")))
        with pytest.raises(httpx.DecodingError):
            probe._get(URL)
        assert responder.calls == 1

    def test_a_redirect_loop_is_not_retried(
        self, probe: Probe, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Configuration, not weather. Three attempts is three loops."""
        responder = _serve(monkeypatch, Responder(httpx.TooManyRedirects("loop")))
        with pytest.raises(httpx.TooManyRedirects):
            probe._get(URL)
        assert responder.calls == 1

    def test_these_really_are_below_httpx_httperror(self) -> None:
        """Proves the two tests above can fail. If they stopped being
        `HTTPError` subclasses they would be excluded for the wrong reason
        and the boundary would go untested again."""
        assert issubclass(httpx.DecodingError, httpx.HTTPError)
        assert issubclass(httpx.TooManyRedirects, httpx.HTTPError)
        assert not issubclass(httpx.DecodingError, httpx.TransportError)
