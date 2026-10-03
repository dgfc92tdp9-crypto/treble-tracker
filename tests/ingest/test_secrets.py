"""Credentials must not survive into an error message.

**This reproduces a real leak rather than a hypothetical one.**
`SourceAdapter._get` carried a docstring stating that an API key could not
reach a message or a log, because the method never adds `params` to one.
`httpx` adds it: it composes the request URL and puts that URL in its own
`HTTPStatusError` message. On 2026-10-03 a 226-symbol populate run hit 429
on six symbols and printed a live Twelve Data key five times, to the console
and into a task log on disk.

So the first test below builds a genuine `httpx.HTTPStatusError` from a
genuine `httpx.Request` with a key in the query string, because a test
against a hand-written string would have passed against the broken code —
the string in that test would have been one *I* wrote, and the whole defect
was that the dangerous string is written by the library.
"""

from __future__ import annotations

import httpx
import pytest

from treble.ingest.secrets import PLACEHOLDER, redact, scrub

#: A fabricated 32-hex string, matching the shape of a Twelve Data key.
#:
#: **It was the real key until 2026-10-03, and that was my mistake.** The
#: leak these tests guard against had just happened, the actual value was in
#: front of me, and I used it as the fixture with a comment reading "shape
#: only; rotated" -- which was not true when it was written. A live
#: credential went into a public repository annotated as already handled,
#: by the commit whose subject was that a key had leaked.
#:
#: The lesson is the session's own, applied to me: a reassuring note is not
#: the thing it describes. A fixture never needs a real secret -- the tests
#: assert that a 32-hex string is removed from a message, and any 32-hex
#: string demonstrates that.
SECRET = "0123456789abcdef0123456789abcdef"  # noqa: S105 - fabricated fixture


class TestTheLeakThatHappened:
    @staticmethod
    def _real_httpx_error() -> httpx.HTTPStatusError:
        url = (
            "https://api.twelvedata.com/time_series?symbol=SPGI&interval=1day"
            f"&outputsize=5000&apikey={SECRET}"
        )
        request = httpx.Request("GET", url)
        response = httpx.Response(429, request=request)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return exc
        raise AssertionError("raise_for_status did not raise on 429")

    def test_httpx_really_does_put_the_key_in_its_message(self) -> None:
        # The premise. If this ever stops holding, the redaction below is
        # guarding nothing and should be re-justified rather than kept out
        # of habit.
        assert SECRET in str(self._real_httpx_error())

    def test_redaction_removes_it(self) -> None:
        redacted = redact(self._real_httpx_error())
        assert SECRET not in str(redacted)
        assert PLACEHOLDER in str(redacted)

    def test_the_useful_part_of_the_message_survives(self) -> None:
        """Redaction must not cost the diagnosis.

        A scrubbed message that no longer says which symbol or which status
        would make the next 429 unactionable, which is how a security fix
        gets reverted.
        """
        message = str(redact(self._real_httpx_error()))
        assert "429" in message
        assert "SPGI" in message
        assert "interval=1day" in message

    def test_the_exception_type_is_preserved(self) -> None:
        # `_get` branches on `exc.response.status_code`, so a redactor that
        # returned a plain Exception would break retry handling.
        redacted = redact(self._real_httpx_error())
        assert isinstance(redacted, httpx.HTTPStatusError)
        assert redacted.response.status_code == 429


class TestScrub:
    @pytest.mark.parametrize(
        "name",
        ["apikey", "api_key", "API_KEY", "key", "token", "access_token", "secret", "password"],
    )
    def test_each_credential_parameter_is_redacted(self, name: str) -> None:
        assert SECRET not in scrub(f"https://x/y?{name}={SECRET}")

    def test_a_symbol_is_not_redacted(self) -> None:
        # `symbol` must survive: it is what makes an error actionable, and a
        # redactor that ate it would be reverted within a week.
        assert "symbol=SPGI" in scrub("https://x/y?symbol=SPGI&apikey=abc")

    def test_redaction_stops_at_the_parameter_boundary(self) -> None:
        out = scrub("https://x/y?apikey=abc&interval=1day&outputsize=5000")
        assert "abc" not in out
        assert "interval=1day" in out
        assert "outputsize=5000" in out

    def test_a_key_in_a_sentence_is_still_redacted(self) -> None:
        # Exception messages are prose with a URL inside, not bare URLs.
        text = f"Client error '429' for url 'https://x/y?apikey={SECRET}'\nFor more information"
        out = scrub(text)
        assert SECRET not in out
        assert "For more information" in out

    def test_several_occurrences_all_go(self) -> None:
        text = " ".join(f"https://x?apikey={SECRET}" for _ in range(5))
        assert scrub(text).count(SECRET) == 0

    def test_text_without_a_secret_is_unchanged(self) -> None:
        clean = "Client error '404' for url 'https://x/y?symbol=NOPE'"
        assert scrub(clean) == clean


class TestRedactEdges:
    def test_an_exception_with_no_args_is_returned(self) -> None:
        exc = ValueError()
        assert redact(exc) is exc

    def test_non_string_args_are_left_alone(self) -> None:
        exc = ValueError(42, None, [1, 2])
        assert redact(exc).args == (42, None, [1, 2])

    def test_mixed_args_redact_only_the_strings(self) -> None:
        exc = ValueError(f"apikey={SECRET}", 7)
        out = redact(exc)
        assert SECRET not in out.args[0]
        assert out.args[1] == 7
