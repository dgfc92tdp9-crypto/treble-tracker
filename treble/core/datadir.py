"""Which store this is, and where it went.

In `core` rather than `store`, and deliberately. Every layer has to agree on
where the data directory is — `cmd.paths` resolves it and `render.server`
reads it — and I7 forbids presentation code from importing `treble.store`.
Putting these signposts in the storage layer made `render -> cmd.paths ->
store` a transitive violation, which the import contract caught the moment
it was introduced. Nothing here touches DuckDB, a payload or a fact: it
reads two small JSON files saying where those things are, which is a
question every layer may legitimately ask.

Two files, both tiny, both solving the same failure: **a workstation that
opens successfully and shows nothing.**

`cmd/paths.py` already records that failure once. The data directory was a
relative `Path("data")`, so launching from anywhere but the repo root
created a fresh empty store and rendered "a screen of honest-looking dashes
with no error". Anchoring the path fixed the cause it had. It does not fix
the cause this module is for.

Moving the store onto another disk reintroduces it by two routes:

1. **The volume is not mounted.** `/Volumes/Treble` exists while the disk
   is attached and not otherwise, so a `mkdir(parents=True, exist_ok=True)`
   either fails obscurely or — if anything else has since created that
   path — quietly builds a second, empty store beside the real one.
2. **Nobody told the workstation.** The bytes move; the default path still
   points at `repo/data`, which is now an empty directory; the next command
   creates a store there and everything reads zero.

The second is the more dangerous, because it needs no hardware fault at
all — only for someone to forget an environment variable.

## The marker

`.treble-store.json` sits in the data directory and says *this is a Treble
store*. Its job is to distinguish a store from a directory that merely
exists, which is the distinction every guard here rests on. It carries an
id so two stores can be told apart, and a label so a person reading it
knows which disk they are holding.

## The pointer

`RELOCATED.json` is left behind in the *old* directory and names the new
one. It means relocation needs no configuration: nothing to set, nothing
to export, nothing to forget. Follow it and you find the store; find it
with the target missing and you can say exactly what is wrong — "the store
was moved to /Volumes/Treble/treble-data; is that volume mounted?" —
instead of rendering an empty screen.

Neither file is authoritative about the *data*. They are signposts, and a
signpost that disagrees with the ground is reported rather than obeyed.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

#: Names a directory as a Treble store. Dot-prefixed so it does not appear
#: in a casual listing beside `payloads/` and `treble.db`, and JSON so a
#: person can read it without this package.
MARKER_NAME = ".treble-store.json"

#: Left in the old directory when a store moves. Not dot-prefixed: someone
#: looking at an apparently empty data directory and wondering where
#: everything went should see it immediately.
POINTER_NAME = "RELOCATED.json"

#: Files and directories that mean a directory holds a store even without a
#: marker — an install that predates this module.
STORE_CONTENTS = ("treble.db", "ingest.db", "payloads", "cold")


class StoreLocationError(Exception):
    """The store is not where it was expected, or is not the one expected.

    Deliberately not a subclass of `FileNotFoundError`: callers that catch
    the latter to mean "no data yet, create some" would swallow exactly the
    case this exists to surface.
    """


@dataclass(frozen=True)
class StoreIdentity:
    """What a marker file says."""

    store_id: str
    created_at: datetime
    label: str

    def to_json(self) -> str:
        return (
            json.dumps(
                {
                    "store_id": self.store_id,
                    "created_at": self.created_at.isoformat(),
                    "label": self.label,
                },
                indent=2,
            )
            + "\n"
        )

    @classmethod
    def from_json(cls, raw: str) -> StoreIdentity | None:
        """Parse a marker, or ``None`` if it is not one.

        Unreadable is treated as absent rather than raised on. A truncated
        marker means an interrupted write, and the recovery for that is to
        write a good one — not to refuse to open a store whose data is
        perfectly intact.
        """
        try:
            data = json.loads(raw)
            return cls(
                store_id=str(data["store_id"]),
                created_at=datetime.fromisoformat(str(data["created_at"])),
                label=str(data.get("label", "")),
            )
        except (ValueError, KeyError, TypeError):
            return None


def new_identity(label: str | None = None) -> StoreIdentity:
    return StoreIdentity(
        store_id=uuid.uuid4().hex,
        created_at=datetime.now(UTC),
        label=label or socket.gethostname(),
    )


def read_marker(data_dir: Path) -> StoreIdentity | None:
    path = data_dir / MARKER_NAME
    try:
        return StoreIdentity.from_json(path.read_text())
    except OSError:
        return None


def write_marker(data_dir: Path, identity: StoreIdentity) -> None:
    (data_dir / MARKER_NAME).write_text(identity.to_json())


def holds_data(data_dir: Path) -> bool:
    """Whether anything of a store is present, marker or not."""
    return any((data_dir / name).exists() for name in STORE_CONTENTS)


def read_pointer(data_dir: Path) -> Path | None:
    """Where this directory says its store went, if it says anything."""
    try:
        data = json.loads((data_dir / POINTER_NAME).read_text())
        moved_to = data.get("moved_to")
    except (OSError, ValueError, AttributeError):
        return None
    return Path(str(moved_to)) if moved_to else None


def write_pointer(old_dir: Path, new_dir: Path, identity: StoreIdentity) -> None:
    old_dir.mkdir(parents=True, exist_ok=True)
    (old_dir / POINTER_NAME).write_text(
        json.dumps(
            {
                "moved_to": str(new_dir),
                "moved_at": datetime.now(UTC).isoformat(),
                "store_id": identity.store_id,
                "note": (
                    "The Treble data directory was moved here. Nothing needs "
                    "configuring — the workstation follows this file. Delete it "
                    "only if you have moved the store back."
                ),
            },
            indent=2,
        )
        + "\n"
    )


def resolve(start: Path) -> Path:
    """Follow relocation pointers from ``start`` to where the store lives.

    Chained moves are followed, so a store relocated twice is still found.
    A loop raises rather than spinning: a pointer cycle is a corrupted
    signpost, and hanging is a worse way to report that than saying so.
    """
    seen: list[Path] = []
    current = start
    while True:
        target = read_pointer(current)
        if target is None:
            return current
        if target in seen or target == current:
            raise StoreLocationError(
                f"relocation pointers form a loop at {target}; "
                f"delete {POINTER_NAME} in the directory that should hold the store"
            )
        seen.append(current)
        current = target


def _points_here(origin: Path, data_dir: Path) -> bool:
    """Whether ``origin``'s relocation chain actually ends at ``data_dir``."""
    if read_pointer(origin) is None:
        return False
    try:
        return resolve(origin) == data_dir
    except StoreLocationError:
        return False


def verify(data_dir: Path, *, origin: Path | None = None) -> None:
    """Refuse to treat ``data_dir`` as a store when it plainly is not one.

    Called after :func:`resolve`, so ``origin`` is where the search
    started — named in the error because "the store is missing" is far less
    useful than "the store was moved to X and X is not there".

    An unmarked directory that already holds data is **adopted**, not
    refused: every install predating this module is in exactly that state,
    and refusing to open a store whose data is intact would be a guard
    doing more harm than the fault it guards against.
    """
    if data_dir.is_dir() and (read_marker(data_dir) or holds_data(data_dir)):
        return
    # Only a *relocation* gets the relocation message — that is, `origin`
    # must actually carry a pointer chain ending here. A caller naming a
    # directory outright (`--data-dir`) is not following a signpost, and
    # telling them their store "was moved" to a path they just typed would
    # be a guard inventing a history. Caught by the CLI suite, which passes
    # a temporary directory to almost every command.
    if origin is not None and origin != data_dir and _points_here(origin, data_dir):
        raise StoreLocationError(
            f"the store was moved to {data_dir}, and nothing is there.\n"
            f"  If it is on an external volume, is that volume mounted?\n"
            f"  The pointer is {origin / POINTER_NAME} — delete it only if you "
            "have moved the store back."
        )
    if os.environ.get("TREBLE_DATA_DIR") and not data_dir.is_dir():
        raise StoreLocationError(
            f"TREBLE_DATA_DIR points at {data_dir}, and nothing is there.\n"
            "  If it is on an external volume, is that volume mounted?\n"
            "  To create a store here deliberately, run `treble init "
            f"--data-dir {data_dir}`."
        )


__all__ = [
    "MARKER_NAME",
    "POINTER_NAME",
    "STORE_CONTENTS",
    "StoreIdentity",
    "StoreLocationError",
    "holds_data",
    "new_identity",
    "read_marker",
    "read_pointer",
    "resolve",
    "verify",
    "write_marker",
    "write_pointer",
]
