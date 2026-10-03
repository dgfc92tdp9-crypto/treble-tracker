"""Keep credentials out of error messages.

**Written because a promise in a docstring was not a mechanism.**
`SourceAdapter._get` stated that an API key could not reach a message or a
log, on the reasoning that the method never adds `params` to one. It does
not have to: `httpx` composes the request URL and puts it in its own
`HTTPStatusError` message. On 2026-10-03 a 226-symbol populate run hit 429
on six symbols and printed a live Twelve Data key five times — to the
console and into a task log on disk.

Nothing reached the payload store or the ingest log, because `source_uri`
genuinely is built without the key. So the durable half of the promise was
kept by construction and the transient half was never checked, which is the
shape of every other failure in this repository's taxonomy: the part with a
mechanism held, the part with only an argument did not.

## Why the parameter *names* rather than the secret values

Scrubbing by value needs the value, which means reading `.env` inside the
error path — so a redactor would hold every credential in the process in
order to remove them, and would silently fail for any secret it had not
been told about. Matching the *names* of credential-bearing query
parameters covers keys this code has never seen, including one a future
adapter introduces without telling anyone.

The names are deliberately broad. A false positive redacts something
harmless from an error message; a false negative publishes a credential.
"""

from __future__ import annotations

import re

#: Query-parameter names whose value is replaced. Matched case-insensitively
#: and as whole parameter names only — `symbol` must not match because a
#: symbol is exactly what an error needs to name to be useful.
SECRET_PARAMS = (
    "apikey",
    "api_key",
    "api-key",
    "key",
    "token",
    "access_token",
    "auth",
    "password",
    "secret",
    "signature",
)

#: What a redacted value becomes. Deliberately not an empty string: an error
#: reading `apikey=` suggests the key was missing, which sends the reader to
#: look for an unset variable instead of at the 429 that actually happened.
PLACEHOLDER = "<redacted>"

_PATTERN = re.compile(
    r"(?i)\b(" + "|".join(re.escape(p) for p in SECRET_PARAMS) + r")=([^&\s\"'<>]+)"
)


def scrub(text: str) -> str:
    """Replace credential-bearing query parameter values in ``text``.

    Operates on the whole string rather than on a parsed URL, because the
    input is an exception message: it contains a URL somewhere inside prose
    and is not itself parseable.
    """
    return _PATTERN.sub(lambda m: f"{m.group(1)}={PLACEHOLDER}", text)


def redact[E: BaseException](exc: E) -> E:
    """Return ``exc`` with credentials removed from its message.

    Mutates `args` in place and returns the same object, rather than
    constructing a replacement. Building a new exception of the same class
    is not generally possible — `httpx.HTTPStatusError` requires `request`
    and `response` keywords, and every vendor exception has its own
    signature — so a redactor that tried would either lose the type or
    raise a `TypeError` while handling an error, which is strictly worse
    than the leak it set out to fix.

    An exception whose `args` are not strings is returned untouched: its
    message cannot be rewritten without inventing one, and a message
    replaced by a guess is harder to act on than a redacted original.
    """
    if not exc.args:
        return exc
    exc.args = tuple(scrub(a) if isinstance(a, str) else a for a in exc.args)
    return exc


__all__ = ["PLACEHOLDER", "SECRET_PARAMS", "redact", "scrub"]
