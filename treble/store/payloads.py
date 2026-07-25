"""Content-addressed raw payload store — invariant I5 (CLAUDE.md §1).

Raw source bytes are stored under their SHA-256 before any parsing happens.
Keys are derived from content, so a payload can never be mutated: writing
identical bytes is an idempotent no-op, and a hash collision with different
bytes (i.e. corruption or a bug) is an error.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import NewType

PayloadHash = NewType("PayloadHash", str)


def payload_hash(data: bytes) -> PayloadHash:
    return PayloadHash(hashlib.sha256(data).hexdigest())


class PayloadIntegrityError(Exception):
    """Stored bytes do not match their content address."""


class PayloadStore:
    """Filesystem payload store: ``root/aa/bb/<sha256>`` (fan-out on first bytes)."""

    def __init__(self, root: Path) -> None:
        self._root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: PayloadHash) -> Path:
        return self._root / key[:2] / key[2:4] / key

    def put(self, data: bytes) -> PayloadHash:
        """Store bytes, returning their content address. Idempotent."""
        key = payload_hash(data)
        path = self._path(key)
        if path.exists():
            # Content-addressing makes overwrite meaningless; verify instead.
            if path.read_bytes() != data:
                raise PayloadIntegrityError(f"stored payload does not match its hash: {key}")
            return key
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.rename(path)  # atomic publish; readers never see partial writes
        return key

    def get(self, key: PayloadHash) -> bytes:
        data = self._path(key).read_bytes()
        if payload_hash(data) != key:
            raise PayloadIntegrityError(f"payload corrupted on disk: {key}")
        return data

    def exists(self, key: PayloadHash) -> bool:
        return self._path(key).exists()
