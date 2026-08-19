"""Write-once archiving with retention and legal hold (P3_2).

**This is not WORM media, and the difference must not be blurred.** SEC
17a-4(f) contemplates storage that is physically incapable of alteration.
This is a directory on a disk, and anyone with filesystem access can delete
it with `rm`. What is enforced here is that *the application* refuses —
which is worth having, and is a smaller claim than the phrase "WORM" usually
carries. A system that implied hardware immutability it could not deliver
would be making a compliance assertion on someone else's behalf.

Three rules, and each exists because the alternative fails silently:

**Write once.** Records are content-addressed, so re-archiving identical
bytes is a no-op and archiving *different* bytes under an existing key is
impossible by construction rather than by check. This is `PayloadStore`'s
property and the reason this builds on it rather than beside it.

**Retention runs from a stated event, not from now.** A record's clock
starts at the date the record concerns — the trade, the message, the end of
the relationship — because that is what the rules count from. Defaulting to
the archive time would start every clock late, and a record archived a year
after the fact would be retained a year too long. Being wrong in the safe
direction is still being wrong about a date somebody may have to certify.

**A legal hold outranks expiry.** A record under hold cannot be deleted even
after its retention has run, because the hold exists precisely for the case
where the schedule says destroy and an obligation says keep. A hold that
expiry could override would be no hold at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from treble.store.payloads import PayloadHash, PayloadStore

#: Seven years, the horizon most books-and-records rules land on. A default
#: rather than a policy: the caller states the period per record because
#: different record classes are retained for different terms, and a single
#: hardcoded number would quietly apply one class's rule to all of them.
DEFAULT_RETENTION_YEARS = 7

#: Where the index lives beside the payloads.
INDEX_FILENAME = "vault-index.json"

VERSION = 1


class VaultError(RuntimeError):
    """The archive refuses."""


class RetentionNotExpiredError(VaultError):
    """Deletion attempted before the retention period has run."""


class LegalHoldError(VaultError):
    """Deletion attempted on a record under legal hold."""


class RecordNotFoundError(VaultError):
    """No archived record under this key."""


@dataclass(frozen=True)
class ArchivedRecord:
    """One archived item and the terms it is held under."""

    key: str
    kind: str
    #: What the record is *about*, which is what the retention clock runs
    #: from. Not the archive time — see the module docstring.
    event_date: date
    archived_at: datetime
    retention_years: int
    legal_hold: bool = False

    @property
    def retained_until(self) -> date:
        """When the schedule permits destruction.

        Computed rather than stored, so a retention period corrected on the
        record is reflected immediately. A stored expiry date would go stale
        the moment anyone amended the term and would keep answering with the
        old one.
        """
        try:
            return self.event_date.replace(year=self.event_date.year + self.retention_years)
        except ValueError:
            # 29 February plus N years where the target is not a leap year.
            # Rolled forward to 1 March rather than back to 28 February:
            # retention must never end *earlier* than the term states.
            return self.event_date.replace(
                year=self.event_date.year + self.retention_years, day=1, month=3
            )

    def expired(self, *, today: date) -> bool:
        return today >= self.retained_until

    def deletable(self, *, today: date) -> bool:
        return self.expired(today=today) and not self.legal_hold


class Vault:
    """A content-addressed archive that refuses early destruction."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self._payloads = PayloadStore(directory / "payloads")
        self._index_path = directory / INDEX_FILENAME
        self._index: dict[str, ArchivedRecord] = {}
        self._load()

    # -- persistence ----------------------------------------------------

    def _load(self) -> None:
        if not self._index_path.exists():
            return
        payload = json.loads(self._index_path.read_text())
        if payload.get("version") != VERSION:
            raise VaultError(
                f"{self._index_path} is version {payload.get('version')} and this build "
                f"writes {VERSION}. Refused rather than opened: retention terms whose "
                "meaning changed between versions would be enforced against the wrong "
                "clock"
            )
        for row in payload["records"]:
            self._index[row["key"]] = ArchivedRecord(
                key=row["key"],
                kind=row["kind"],
                event_date=date.fromisoformat(row["event_date"]),
                archived_at=datetime.fromisoformat(row["archived_at"]),
                retention_years=int(row["retention_years"]),
                legal_hold=bool(row["legal_hold"]),
            )

    def _save(self) -> None:
        payload = {
            "version": VERSION,
            "records": [
                {
                    "key": record.key,
                    "kind": record.kind,
                    "event_date": record.event_date.isoformat(),
                    "archived_at": record.archived_at.isoformat(),
                    "retention_years": record.retention_years,
                    "legal_hold": record.legal_hold,
                }
                for record in self._index.values()
            ],
        }
        temporary = self._index_path.with_suffix(".partial")
        temporary.write_text(json.dumps(payload, indent=2))
        temporary.replace(self._index_path)

    # -- archiving ------------------------------------------------------

    def archive(
        self,
        data: bytes,
        *,
        kind: str,
        event_date: date,
        retention_years: int = DEFAULT_RETENTION_YEARS,
        now: datetime | None = None,
    ) -> ArchivedRecord:
        """Store bytes under their own hash and record the terms.

        Re-archiving identical bytes returns the existing record rather than
        replacing it — including its original terms. Replacing them would
        let a later write shorten an earlier record's retention, which is
        destruction by another route.
        """
        key = str(self._payloads.put(data))
        existing = self._index.get(key)
        if existing is not None:
            return existing
        record = ArchivedRecord(
            key=key,
            kind=kind,
            event_date=event_date,
            archived_at=(now or datetime.now(UTC)).astimezone(UTC),
            retention_years=retention_years,
        )
        self._index[key] = record
        self._save()
        return record

    def read(self, key: str) -> bytes:
        if key not in self._index:
            raise RecordNotFoundError(f"no archived record under {key}")
        return self._payloads.get(PayloadHash(key))

    def record(self, key: str) -> ArchivedRecord:
        if key not in self._index:
            raise RecordNotFoundError(f"no archived record under {key}")
        return self._index[key]

    # -- holds and destruction ------------------------------------------

    def place_hold(self, key: str) -> ArchivedRecord:
        """Put a record beyond destruction until the hold is lifted."""
        held = self.record(key)
        self._index[key] = ArchivedRecord(
            key=held.key,
            kind=held.kind,
            event_date=held.event_date,
            archived_at=held.archived_at,
            retention_years=held.retention_years,
            legal_hold=True,
        )
        self._save()
        return self._index[key]

    def lift_hold(self, key: str) -> ArchivedRecord:
        held = self.record(key)
        self._index[key] = ArchivedRecord(
            key=held.key,
            kind=held.kind,
            event_date=held.event_date,
            archived_at=held.archived_at,
            retention_years=held.retention_years,
            legal_hold=False,
        )
        self._save()
        return self._index[key]

    def destroy(self, key: str, *, today: date | None = None) -> None:
        """Delete a record, or refuse and say which rule stopped it.

        The two refusals are deliberately distinct exceptions. "Not yet" and
        "under hold" call for different actions — wait, or go and ask
        counsel — and a single error type would leave the caller unable to
        tell which.
        """
        record = self.record(key)
        when = today or datetime.now(UTC).date()
        if record.legal_hold:
            raise LegalHoldError(
                f"{key} is under legal hold and cannot be destroyed, retained until "
                f"{record.retained_until} or not. A hold that expiry could override "
                "would be no hold at all"
            )
        if not record.expired(today=when):
            raise RetentionNotExpiredError(
                f"{key} is retained until {record.retained_until} and today is {when}. "
                f"Its {record.retention_years}-year term runs from {record.event_date}, "
                "the date the record concerns, not the date it was archived"
            )
        # The payload is left in place deliberately: it is content-addressed
        # and may be referenced by another record archived from the same
        # bytes. Removing the index entry is what ends this record's
        # retention; a shared payload's last reader is a garbage-collection
        # question, not a compliance one.
        del self._index[key]
        self._save()

    def due_for_destruction(self, *, today: date | None = None) -> tuple[ArchivedRecord, ...]:
        """Records the schedule permits destroying, holds excluded.

        Returned rather than acted on. Destruction is not something software
        should do on a timer without a person choosing to — the schedule
        says *may*, not *must*.
        """
        when = today or datetime.now(UTC).date()
        return tuple(record for record in self._index.values() if record.deletable(today=when))

    def under_hold(self) -> tuple[ArchivedRecord, ...]:
        return tuple(record for record in self._index.values() if record.legal_hold)

    def __len__(self) -> int:
        return len(self._index)


__all__ = [
    "DEFAULT_RETENTION_YEARS",
    "VERSION",
    "ArchivedRecord",
    "LegalHoldError",
    "RecordNotFoundError",
    "RetentionNotExpiredError",
    "Vault",
    "VaultError",
]
