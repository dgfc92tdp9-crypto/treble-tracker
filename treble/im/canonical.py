"""Matrix canonical JSON, in one place.

Two things in this package need it and they need it for different reasons,
which is exactly why it lives here rather than in either of them:

* `crosssigning` signs an object, and signs it with `signatures` and
  `unsigned` removed — an object cannot contain its own signature;
* `sas` hashes a `start` event to commit to it, and hashes it whole,
  because the commitment is over what was actually sent.

Those are different transformations of the same serialisation. Keeping the
serialisation shared and the stripping separate means one statement of what
"canonical" means, and no chance of the two drifting to different
separators or a different `ensure_ascii` — a divergence that would not look
wrong anywhere, and would simply make every signature fail to verify
against every other client.

**Not `render.contract.buffer.canonical_json`.** That one indents for
readable layout goldens, which is right there and fatal here. The names are
similar enough that sharing them would look like sensible deduplication,
which is why both modules say so.
"""

from __future__ import annotations

import json
from typing import Any

#: Members excluded when an object is serialised *for signing*, per the
#: Matrix specification: `signatures` because an object cannot contain its
#: own signature, `unsigned` because it is server-supplied metadata the
#: signer never saw.
UNSIGNED_MEMBERS = ("signatures", "unsigned")


def canonical_json(payload: dict[str, Any]) -> bytes:
    """The Matrix canonical serialisation: sorted, compact, UTF-8.

    `ensure_ascii=False` so a non-ASCII display name is serialised as the
    bytes the specification says rather than as `\\uXXXX` escapes. The two
    produce different hashes and different signatures, and only one of them
    agrees with any other client.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def signing_bytes(payload: dict[str, Any]) -> bytes:
    """Canonical JSON of ``payload`` with the unsigned members removed.

    What a signature is computed over. Separate from :func:`canonical_json`
    rather than a flag on it, because the choice is not a formatting option
    — signing the wrong one produces a signature that never verifies, and a
    boolean parameter is how a caller picks the wrong one by accident.
    """
    return canonical_json({k: v for k, v in payload.items() if k not in UNSIGNED_MEMBERS})


__all__ = ["UNSIGNED_MEMBERS", "canonical_json", "signing_bytes"]
