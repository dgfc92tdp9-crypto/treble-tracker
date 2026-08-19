"""Content-addressed raw payload store — invariant I5 (CLAUDE.md §1).

Raw source bytes are stored under their SHA-256 before any parsing happens.
Keys are derived from content, so a payload can never be mutated: writing
identical bytes is an idempotent no-op, and a hash collision with different
bytes (i.e. corruption or a bug) is an error.

**Payloads are stored gzipped, and the key is still the hash of the
*original* bytes.** That distinction is the whole design. Hashing the
compressed form would have been simpler and would have invalidated every
`provenance.payload_hash` already in the store — millions of facts pointing
at addresses that no longer resolve — and worse, it would make the address
depend on the compression level, so re-compressing a payload would move it.
The address is a property of the source's bytes, not of how this repository
chose to keep them.

`get` therefore returns exactly what the source served, verified against
that same hash on every read. Compression is invisible above this file: I5
replay reproduces the identical fact set because it re-parses identical
bytes.

**A mixed store is readable.** Payloads written before this change sit
uncompressed, and `get` reads either form. That is what makes the migration
interruptible: stopping it half way leaves a working store rather than a
broken one, which matters when the reason for running it is that the disk is
full.

Measured on the live store: 1.7GB of EDGAR XML, N-PORT filings, FRED and
Treasury CSV and GLEIF archives. Text of that kind compresses several times
over; already-compressed payloads (the GLEIF zip) do not, and are stored
gzipped anyway rather than special-cased — a size check that chose per file
would be another branch to get wrong, and gzip on incompressible input costs
a few bytes rather than a few percent.
"""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path
from typing import NewType

PayloadHash = NewType("PayloadHash", str)

#: Maximum, because payloads are written once and read rarely. The CPU cost
#: lands on ingest, which is already network-bound.
COMPRESSION_LEVEL = 9

#: Appended to the content address on disk. The address itself is unchanged.
SUFFIX = ".gz"


def payload_hash(data: bytes) -> PayloadHash:
    return PayloadHash(hashlib.sha256(data).hexdigest())


class PayloadIntegrityError(Exception):
    """Stored bytes do not match their content address."""


class PayloadStore:
    """Filesystem payload store: ``root/aa/bb/<sha256>.gz`` (fan-out on first bytes)."""

    def __init__(self, root: Path) -> None:
        self._root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: PayloadHash) -> Path:
        """Where an *uncompressed* payload lives. Legacy reads only."""
        return self._root / key[:2] / key[2:4] / key

    def _gz_path(self, key: PayloadHash) -> Path:
        path = self._path(key)
        return path.parent / (path.name + SUFFIX)

    def put(self, data: bytes) -> PayloadHash:
        """Store bytes, returning their content address. Idempotent."""
        key = payload_hash(data)
        if self.exists(key):
            # Content-addressing makes overwrite meaningless; verify instead.
            # Compared through `get`, so an uncompressed legacy payload and a
            # compressed one are checked the same way.
            if self.get(key) != data:
                raise PayloadIntegrityError(f"stored payload does not match its hash: {key}")
            return key
        path = self._gz_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / (path.name + ".tmp")
        tmp.write_bytes(gzip.compress(data, compresslevel=COMPRESSION_LEVEL))
        tmp.rename(path)  # atomic publish; readers never see partial writes
        return key

    def get(self, key: PayloadHash) -> bytes:
        """The original bytes, verified against their content address.

        Reads the compressed form if present and the uncompressed one
        otherwise, so a store part-way through migration answers for every
        payload it holds.
        """
        gz = self._gz_path(key)
        if gz.exists():
            try:
                data = gzip.decompress(gz.read_bytes())
            except (OSError, EOFError) as error:
                # A truncated or corrupt archive is an I5 failure and reads
                # as one: the facts derived from this payload can no longer
                # be reproduced.
                raise PayloadIntegrityError(
                    f"payload {key} could not be decompressed: {error}. The facts parsed "
                    "from it can no longer be reproduced"
                ) from error
        else:
            data = self._path(key).read_bytes()
        if payload_hash(data) != key:
            raise PayloadIntegrityError(f"payload corrupted on disk: {key}")
        return data

    def exists(self, key: PayloadHash) -> bool:
        return self._gz_path(key).exists() or self._path(key).exists()

    def compress_existing(self, *, limit: int | None = None) -> tuple[int, int, int]:
        """Compress uncompressed payloads in place, one at a time.

        Returns `(files, bytes_before, bytes_after)`.

        **One file at a time, verified before the original is removed.** The
        reason for running this is usually that the disk is full, so a
        migration needing room for a second copy of the whole store would be
        unusable exactly when it is needed. Each file is compressed, read
        back, checked against its content address, and only then is the
        original unlinked — so an interruption at any point leaves either the
        original or a verified replacement, never neither.
        """
        files = before = after = 0
        for path in sorted(self._root.rglob("*")):
            if not path.is_file() or path.name.endswith((SUFFIX, ".tmp")):
                continue
            key = PayloadHash(path.name)
            if len(key) != 64:
                continue
            original = path.read_bytes()
            if payload_hash(original) != key:
                raise PayloadIntegrityError(
                    f"refusing to compress {key}: it does not match its own address, so "
                    "the store is already damaged and compressing would hide it"
                )
            gz = self._gz_path(key)
            tmp = gz.parent / (gz.name + ".tmp")
            tmp.write_bytes(gzip.compress(original, compresslevel=COMPRESSION_LEVEL))
            tmp.rename(gz)
            # Read back through the public path before destroying the source.
            if self.get(key) != original:
                gz.unlink()
                raise PayloadIntegrityError(f"round-trip failed for {key}; original left in place")
            path.unlink()
            files += 1
            before += len(original)
            after += gz.stat().st_size
            if limit is not None and files >= limit:
                break
        return files, before, after


__all__ = [
    "COMPRESSION_LEVEL",
    "SUFFIX",
    "PayloadHash",
    "PayloadIntegrityError",
    "PayloadStore",
    "payload_hash",
]
