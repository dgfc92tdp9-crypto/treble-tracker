"""Projecting what the declared cadences will cost the disk.

The estimate is deliberately built from what has *actually* been fetched
rather than from documentation, so these tests are about the arithmetic
being honest under the awkward cases: a source fetched once, a source that
returned identical bytes twice, and a source with no declared cadence at
all.
"""

from __future__ import annotations

import gzip
import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

from treble.ingest.growth import RECENT_PAYLOADS, payload_sizes, project
from treble.store.ingest_log import IngestLog

#: A source declaring a one-day cadence, and one declaring none. Both real,
#: because the projection reads the registry and a made-up id would be
#: silently skipped as "no cadence" and prove nothing.
DAILY = "fred"
NO_CADENCE = "openfigi"


def _store_payload(root: Path, body: bytes) -> str:
    """Write a payload the way the content-addressed store does."""
    digest = hashlib.sha256(body).hexdigest()
    path = root / digest[:2] / digest[2:4] / f"{digest}.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(body))
    return digest


def _log(tmp_path: Path) -> IngestLog:
    return IngestLog(tmp_path / "ingest.db")


def _record(log: IngestLog, source: str, digest: str, when: int) -> None:
    log.append(
        source=source,
        payload_hash=digest,
        source_uri=f"https://example.invalid/{source}/{when}",
        fetched_at=datetime(2026, 9, when, tzinfo=UTC),
        parser_version="1",
    )


class TestProjection:
    def test_a_daily_source_contributes_one_payload_per_day(self, tmp_path: Path) -> None:
        payloads = tmp_path / "payloads"
        digest = _store_payload(payloads, b"x" * 10_000)
        log = _log(tmp_path)
        _record(log, DAILY, digest, 1)

        growth = project(log, payloads)
        stored = payload_sizes(payloads)[digest]
        assert growth.per_day == stored
        assert growth.contributors == ((DAILY, stored),)

    def test_identical_bytes_fetched_twice_count_once(self, tmp_path: Path) -> None:
        """The content-addressed store keeps one file for them. Averaging
        over log entries instead of distinct payloads would inflate the
        estimate by exactly the amount that store just saved."""
        payloads = tmp_path / "payloads"
        digest = _store_payload(payloads, b"x" * 10_000)
        log = _log(tmp_path)
        _record(log, DAILY, digest, 1)
        _record(log, DAILY, digest, 2)

        assert project(log, payloads).per_day == payload_sizes(payloads)[digest]

    def test_a_source_with_no_declared_cadence_is_not_guessed_at(self, tmp_path: Path) -> None:
        """`health.py` refuses to invent an expectation for these, and
        inventing one here would put a number on the disk report that the
        freshness report explicitly declines to stand behind."""
        payloads = tmp_path / "payloads"
        digest = _store_payload(payloads, b"y" * 50_000)
        log = _log(tmp_path)
        _record(log, NO_CADENCE, digest, 1)

        growth = project(log, payloads)
        assert growth.per_day == 0
        assert growth.contributors == ()

    def test_a_logged_payload_missing_from_disk_is_skipped(self, tmp_path: Path) -> None:
        """The log is append-only and payloads can be moved to another
        volume. Counting a file that is not there as zero is right;
        crashing on it would make `treble storage` fail on exactly the
        install that had done the recommended thing."""
        payloads = tmp_path / "payloads"
        payloads.mkdir()
        log = _log(tmp_path)
        _record(log, DAILY, "0" * 64, 1)

        assert project(log, payloads).per_day == 0

    def test_contributors_are_largest_first(self, tmp_path: Path) -> None:
        """The list exists to be read down and stopped at. Unordered, the
        biggest cost could sit anywhere in it."""
        payloads = tmp_path / "payloads"
        log = _log(tmp_path)
        _record(log, DAILY, _store_payload(payloads, b"a" * 100_000), 1)
        _record(log, "ecb-fx", _store_payload(payloads, b"b" * 10_000), 1)

        sources = [source for source, _ in project(log, payloads).contributors]
        assert sources == [DAILY, "ecb-fx"]

    def test_an_empty_log_projects_nothing(self, tmp_path: Path) -> None:
        payloads = tmp_path / "payloads"
        payloads.mkdir()
        assert project(_log(tmp_path), payloads).per_day == 0

    def test_a_missing_payload_directory_is_not_an_error(self, tmp_path: Path) -> None:
        assert payload_sizes(tmp_path / "never-created") == {}


class TestTheEstimateFollowsRecentFetches:
    """An all-history mean cannot see a change in *how* a source fetches.

    `gleif-rr` moved from 37 MB full copies to 90 KB deltas and the mean
    over its whole history still projected 19.4 MB/day — true of the past,
    wrong about every day to come, and the runway figure is read to make a
    decision about the future.

    Payload bodies here are random rather than repeated bytes: the store
    gzips, and 100,000 identical characters land on disk as about 130. A
    first draft of these tests measured compression instead of size.
    """

    BIG = 200_000
    SMALL = 2_000

    def _run(self, tmp_path: Path, old: int, new: int) -> int:
        payloads = tmp_path / "payloads"
        log = _log(tmp_path)
        day = 1
        for _ in range(old):
            _record(log, DAILY, _store_payload(payloads, os.urandom(self.BIG)), day)
            day += 1
        for _ in range(new):
            _record(log, DAILY, _store_payload(payloads, os.urandom(self.SMALL)), day)
            day += 1
        return project(log, payloads).per_day

    def test_old_large_payloads_fall_out_of_the_window(self, tmp_path: Path) -> None:
        per_day = self._run(tmp_path, old=3, new=RECENT_PAYLOADS)
        assert per_day < self.BIG / 10, f"still averaging in the old full copies: {per_day:,}"

    def test_a_partly_migrated_source_is_between_the_two(self, tmp_path: Path) -> None:
        """Not a cliff. Two new payloads among three old ones should pull
        the estimate down without pretending the change is complete."""
        per_day = self._run(tmp_path, old=3, new=2)
        assert self.SMALL < per_day < self.BIG

    def test_a_source_with_fewer_payloads_than_the_window_still_projects(
        self, tmp_path: Path
    ) -> None:
        """Slicing the tail of a short list must not yield nothing."""
        payloads = tmp_path / "payloads"
        log = _log(tmp_path)
        digest = _store_payload(payloads, os.urandom(self.SMALL))
        _record(log, DAILY, digest, 1)
        assert project(log, payloads).per_day == payload_sizes(payloads)[digest]

    def test_the_window_is_the_most_recent_not_the_first(self, tmp_path: Path) -> None:
        """Proves the slice takes the tail. Reversed, the estimate would
        lock onto whatever a source did when it was first written."""
        payloads = tmp_path / "payloads"
        log = _log(tmp_path)
        _record(log, DAILY, _store_payload(payloads, os.urandom(self.SMALL)), 1)
        for day in range(2, 2 + RECENT_PAYLOADS):
            _record(log, DAILY, _store_payload(payloads, os.urandom(self.BIG)), day)
        assert project(log, payloads).per_day > self.BIG / 2
