"""Sequence numbers that survive a restart (P3_3).

A FIX session counts every message in both directions, and the counters are
**per session, not per connection**. A process that restarts and begins again
at 1 while the counterparty expects 47 is not resuming a session; it is
claiming forty-six messages never happened. The peer either rejects the
Logon or — worse, depending on its configuration — accepts it and both sides
proceed with different ideas of what has been delivered.

So the counters are written to disk, and the write is atomic for the reason
`render/layout.py` writes atomically: a file half-written by an interrupted
save leaves a session that *looks* resumable and is not, which is worse than
no file at all. With no file the session starts fresh and says so; with a
truncated one it starts wrong and says nothing.

**Written after every message, not on shutdown.** A crash is precisely the
case this exists for, and a counter flushed at exit is a counter that is
correct except when it matters. The cost is one small file write per
message, which is nothing beside the cost of being wrong about a fill.
"""

from __future__ import annotations

import json
from pathlib import Path

from treble.ems.session import Session

#: One file per session, named for the pair it belongs to. A single shared
#: file would make two sessions overwrite each other's counters, and the
#: symptom would be a sequence error on whichever reconnected second.
FILENAME = "fix-{sender}-{target}.json"

#: Bumped if the on-disk shape changes. A version this build does not
#: understand is refused rather than guessed at, exactly as a saved layout
#: is: resuming from a file whose meaning has changed would put a session at
#: a sequence number nobody chose.
VERSION = 1


class SessionStateError(ValueError):
    """The stored state cannot be trusted to resume from."""


def state_path(directory: Path, *, sender: str, target: str) -> Path:
    return directory / FILENAME.format(sender=sender, target=target)


def save(session: Session, directory: Path) -> None:
    """Persist the counters, atomically."""
    directory.mkdir(parents=True, exist_ok=True)
    path = state_path(directory, sender=session.sender, target=session.target)
    payload = {
        "version": VERSION,
        "sender": session.sender,
        "target": session.target,
        "outbound_seq": session.outbound_seq,
        "inbound_seq": session.inbound_seq,
    }
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(payload, indent=2))
    temporary.replace(path)


def resume(directory: Path, *, sender: str, target: str) -> Session:
    """Rebuild a session from disk, or start a fresh one.

    A missing file is a new session and not an error — a first connection has
    nothing to resume. What is refused is a file that exists and cannot be
    trusted: an unknown version, or one recording a different pair.

    **`logged_on` is deliberately not restored.** A session resumes its
    counters, never its authentication: the connection is gone, and treating
    a remembered logon as a live one would let a business message through
    before the peer had identified itself on this connection.
    """
    path = state_path(directory, sender=sender, target=target)
    if not path.exists():
        return Session(sender=sender, target=target)
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise SessionStateError(
            f"{path} is not readable JSON: {error}. A truncated state file leaves a "
            "session that looks resumable and is not — delete it to start fresh, which "
            "is a decision rather than a guess"
        ) from error
    if payload.get("version") != VERSION:
        raise SessionStateError(
            f"{path} is version {payload.get('version')} and this build writes {VERSION}. "
            "Refused rather than opened: resuming from a shape whose meaning changed "
            "would put the session at a sequence number nobody chose"
        )
    if payload.get("sender") != sender or payload.get("target") != target:
        raise SessionStateError(
            f"{path} records {payload.get('sender')}/{payload.get('target')} and this "
            f"session is {sender}/{target}. Two sessions sharing one file overwrite each "
            "other's counters"
        )
    return Session(
        sender=sender,
        target=target,
        outbound_seq=int(payload["outbound_seq"]),
        inbound_seq=int(payload["inbound_seq"]),
    )


__all__ = [
    "FILENAME",
    "VERSION",
    "SessionStateError",
    "resume",
    "save",
    "state_path",
]
