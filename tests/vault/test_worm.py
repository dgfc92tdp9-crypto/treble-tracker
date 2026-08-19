"""WORM archiving with retention and legal hold (P3_2).

**This is not WORM media and the tests do not pretend otherwise.** SEC
17a-4(f) contemplates storage physically incapable of alteration; this is a
directory on a disk and `rm` defeats it. What is asserted here is that the
*application* refuses, which is a smaller and honest claim.

The retention arithmetic is tested harder than the storage, because the
storage is content-addressed and therefore correct by construction, while a
date computed wrongly refuses destruction on the wrong day — in either
direction, and silently.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.vault.worm import (
    DEFAULT_RETENTION_YEARS,
    LegalHoldError,
    RecordNotFoundError,
    RetentionNotExpiredError,
    Vault,
    VaultError,
)

EVENT = date(2020, 6, 30)
ARCHIVED = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def _vault(tmp_path: Path, name: str = "v") -> Vault:
    return Vault(tmp_path / name)


class TestWriteOnce:
    def test_bytes_come_back_exactly(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        record = vault.archive(b"an execution report", kind="fix", event_date=EVENT, now=ARCHIVED)
        assert vault.read(record.key) == b"an execution report"

    def test_archiving_the_same_bytes_twice_is_one_record(self, tmp_path: Path) -> None:
        """Content-addressed, so a repeat is a no-op rather than a second
        copy under a second clock."""
        vault = _vault(tmp_path)
        first = vault.archive(b"same", kind="fix", event_date=EVENT, now=ARCHIVED)
        second = vault.archive(b"same", kind="fix", event_date=EVENT, now=ARCHIVED)
        assert first.key == second.key
        assert len(vault) == 1

    def test_a_repeat_cannot_shorten_the_original_terms(self, tmp_path: Path) -> None:
        """Re-archiving with a shorter retention must not replace the terms:
        that is destruction by another route, and it would look like an
        ordinary write."""
        vault = _vault(tmp_path)
        original = vault.archive(
            b"same", kind="fix", event_date=EVENT, retention_years=7, now=ARCHIVED
        )
        repeat = vault.archive(
            b"same", kind="fix", event_date=EVENT, retention_years=1, now=ARCHIVED
        )
        assert repeat.retention_years == original.retention_years == 7

    def test_an_unknown_key_is_an_error_not_empty_bytes(self, tmp_path: Path) -> None:
        with pytest.raises(RecordNotFoundError):
            _vault(tmp_path).read("0" * 64)


class TestRetentionRunsFromTheEvent:
    def test_the_clock_starts_at_the_record_s_own_date(self, tmp_path: Path) -> None:
        """Not the archive time. A record archived six years late would
        otherwise be retained six years too long — being wrong in the safe
        direction is still being wrong about a date somebody may certify."""
        record = _vault(tmp_path).archive(
            b"x", kind="fix", event_date=EVENT, retention_years=7, now=ARCHIVED
        )
        assert record.retained_until == date(2027, 6, 30)
        assert record.archived_at.year == 2026

    def test_the_default_term_is_seven_years(self, tmp_path: Path) -> None:
        record = _vault(tmp_path).archive(b"x", kind="fix", event_date=EVENT, now=ARCHIVED)
        assert record.retention_years == DEFAULT_RETENTION_YEARS

    def test_a_leap_day_event_rolls_forward_never_back(self, tmp_path: Path) -> None:
        """29 February plus seven years has no 29 February to land on.
        Rolling back to the 28th would end retention a day early, which is
        the one direction that cannot be allowed."""
        record = _vault(tmp_path).archive(
            b"leap", kind="fix", event_date=date(2020, 2, 29), retention_years=7, now=ARCHIVED
        )
        assert record.retained_until == date(2027, 3, 1)
        assert record.retained_until > date(2027, 2, 28)

    def test_expiry_is_inclusive_of_the_day_itself(self, tmp_path: Path) -> None:
        record = _vault(tmp_path).archive(
            b"x", kind="fix", event_date=EVENT, retention_years=7, now=ARCHIVED
        )
        assert not record.expired(today=date(2027, 6, 29))
        assert record.expired(today=date(2027, 6, 30))


class TestDestructionIsRefused:
    def test_destroying_before_expiry_is_refused(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        record = vault.archive(b"x", kind="fix", event_date=EVENT, now=ARCHIVED)
        with pytest.raises(RetentionNotExpiredError, match="retained until"):
            vault.destroy(record.key, today=date(2026, 8, 11))
        assert len(vault) == 1

    def test_the_refusal_names_the_date_the_clock_runs_from(self, tmp_path: Path) -> None:
        """A refusal that gave only "not yet" would leave the caller unable
        to check whether the clock itself is right."""
        vault = _vault(tmp_path)
        record = vault.archive(b"x", kind="fix", event_date=EVENT, now=ARCHIVED)
        with pytest.raises(RetentionNotExpiredError, match="2020-06-30"):
            vault.destroy(record.key, today=date(2026, 8, 11))

    def test_destroying_after_expiry_is_permitted(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        record = vault.archive(b"x", kind="fix", event_date=EVENT, now=ARCHIVED)
        vault.destroy(record.key, today=date(2028, 1, 1))
        assert len(vault) == 0

    def test_a_destroyed_record_is_gone_from_the_index(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        record = vault.archive(b"x", kind="fix", event_date=EVENT, now=ARCHIVED)
        vault.destroy(record.key, today=date(2028, 1, 1))
        with pytest.raises(RecordNotFoundError):
            vault.read(record.key)


class TestLegalHoldOutranksExpiry:
    """The rule that exists precisely for when the schedule says destroy and
    an obligation says keep."""

    def test_a_held_record_survives_its_own_expiry(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        record = vault.archive(b"x", kind="fix", event_date=EVENT, now=ARCHIVED)
        vault.place_hold(record.key)
        with pytest.raises(LegalHoldError, match="legal hold"):
            vault.destroy(record.key, today=date(2099, 1, 1))
        assert len(vault) == 1

    def test_the_two_refusals_are_distinguishable(self, tmp_path: Path) -> None:
        """ "Not yet" and "under hold" call for different actions — wait, or
        go and ask counsel — and one exception type would leave the caller
        unable to tell which."""
        assert not issubclass(LegalHoldError, RetentionNotExpiredError)
        assert not issubclass(RetentionNotExpiredError, LegalHoldError)
        assert issubclass(LegalHoldError, VaultError)

    def test_hold_is_checked_before_expiry(self, tmp_path: Path) -> None:
        """A held record that is *also* unexpired must report the hold: it
        is the stronger and longer-lived reason, and reporting the date
        would invite waiting for a day that changes nothing."""
        vault = _vault(tmp_path)
        record = vault.archive(b"x", kind="fix", event_date=EVENT, now=ARCHIVED)
        vault.place_hold(record.key)
        with pytest.raises(LegalHoldError):
            vault.destroy(record.key, today=date(2026, 8, 11))

    def test_lifting_a_hold_restores_the_schedule(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        record = vault.archive(b"x", kind="fix", event_date=EVENT, now=ARCHIVED)
        vault.place_hold(record.key)
        vault.lift_hold(record.key)
        vault.destroy(record.key, today=date(2028, 1, 1))
        assert len(vault) == 0

    def test_held_records_are_listable(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        held = vault.archive(b"a", kind="fix", event_date=EVENT, now=ARCHIVED)
        vault.archive(b"b", kind="fix", event_date=EVENT, now=ARCHIVED)
        vault.place_hold(held.key)
        assert [r.key for r in vault.under_hold()] == [held.key]


class TestTheScheduleIsReportedNotActed:
    def test_due_records_are_listed(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        vault.archive(b"old", kind="fix", event_date=date(2015, 1, 1), now=ARCHIVED)
        vault.archive(b"new", kind="fix", event_date=date(2025, 1, 1), now=ARCHIVED)
        due = vault.due_for_destruction(today=date(2026, 8, 11))
        assert [r.event_date for r in due] == [date(2015, 1, 1)]

    def test_listing_does_not_destroy(self, tmp_path: Path) -> None:
        """The schedule says *may*, not *must*. Destruction on a timer with
        nobody choosing is not something this should do."""
        vault = _vault(tmp_path)
        vault.archive(b"old", kind="fix", event_date=date(2015, 1, 1), now=ARCHIVED)
        vault.due_for_destruction(today=date(2026, 8, 11))
        assert len(vault) == 1

    def test_held_records_are_never_listed_as_due(self, tmp_path: Path) -> None:
        vault = _vault(tmp_path)
        record = vault.archive(b"old", kind="fix", event_date=date(2015, 1, 1), now=ARCHIVED)
        vault.place_hold(record.key)
        assert vault.due_for_destruction(today=date(2026, 8, 11)) == ()


class TestItSurvivesARestart:
    def test_terms_and_holds_reload(self, tmp_path: Path) -> None:
        first = Vault(tmp_path / "v")
        record = first.archive(b"x", kind="fix", event_date=EVENT, retention_years=3, now=ARCHIVED)
        first.place_hold(record.key)

        second = Vault(tmp_path / "v")
        reloaded = second.record(record.key)
        assert reloaded.retention_years == 3
        assert reloaded.legal_hold
        assert reloaded.event_date == EVENT

    def test_an_unknown_index_version_is_refused(self, tmp_path: Path) -> None:
        """Retention terms whose meaning changed between versions would be
        enforced against the wrong clock."""
        import json

        vault = Vault(tmp_path / "v")
        vault.archive(b"x", kind="fix", event_date=EVENT, now=ARCHIVED)
        index = tmp_path / "v" / "vault-index.json"
        payload = json.loads(index.read_text())
        payload["version"] = 99
        index.write_text(json.dumps(payload))
        with pytest.raises(VaultError, match="version 99"):
            Vault(tmp_path / "v")
