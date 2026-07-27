"""The company index cache — what makes the workstation openable offline.

Ticker resolution needs EDGAR's company index, and fetching it on every
launch made opening the application depend on EDGAR being reachable. A
desktop app that cannot open on a train is broken, so these pin the
behaviour that fixed it.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from treble.ingest import populate
from treble.ingest.populate import cached_company_index

PAYLOAD = b'{"0": {"cik_str": 51143, "ticker": "IBM", "title": "INTERNATIONAL BUSINESS MACHINES"}}'


class TestCaching:
    def test_first_call_fetches_and_writes_the_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        def fake_fetch(email: str) -> bytes:
            calls.append(email)
            return PAYLOAD

        monkeypatch.setattr(populate, "fetch_company_index", fake_fetch)
        assert cached_company_index(tmp_path, "a@b.com") == PAYLOAD
        assert (tmp_path / "company_index.json").read_bytes() == PAYLOAD
        assert calls == ["a@b.com"]

    def test_fresh_cache_is_used_without_the_network(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "company_index.json").write_bytes(PAYLOAD)

        def explode(email: str) -> bytes:
            raise AssertionError("must not touch the network when the cache is fresh")

        monkeypatch.setattr(populate, "fetch_company_index", explode)
        assert cached_company_index(tmp_path, "a@b.com") == PAYLOAD

    def test_stale_cache_is_refreshed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache = tmp_path / "company_index.json"
        cache.write_bytes(b"old")
        stale = time.time() - 48 * 3600
        import os

        os.utime(cache, (stale, stale))

        monkeypatch.setattr(populate, "fetch_company_index", lambda email: PAYLOAD)
        assert cached_company_index(tmp_path, "a@b.com") == PAYLOAD
        assert cache.read_bytes() == PAYLOAD


class TestOffline:
    def test_stale_cache_beats_no_workstation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Offline with a stale cache must still open. Tickers change far
        more slowly than a day, so serving yesterday's index is right and
        refusing to start is not."""
        cache = tmp_path / "company_index.json"
        cache.write_bytes(PAYLOAD)
        stale = time.time() - 48 * 3600
        import os

        os.utime(cache, (stale, stale))

        def offline(email: str) -> bytes:
            raise OSError("network is unreachable")

        monkeypatch.setattr(populate, "fetch_company_index", offline)
        assert cached_company_index(tmp_path, "a@b.com") == PAYLOAD

    def test_offline_with_no_cache_reports_the_real_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The one unopenable state is "never once online", and it must
        say so rather than fail somewhere further in."""

        def offline(email: str) -> bytes:
            raise OSError("network is unreachable")

        monkeypatch.setattr(populate, "fetch_company_index", offline)
        with pytest.raises(OSError, match="network is unreachable"):
            cached_company_index(tmp_path, "a@b.com")

    def test_interrupted_write_cannot_leave_a_truncated_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A half-written cache would be served as though it were valid,
        so the write goes through a temporary file and a rename."""
        monkeypatch.setattr(populate, "fetch_company_index", lambda email: PAYLOAD)
        cached_company_index(tmp_path, "a@b.com")
        assert not list(tmp_path.glob("*.tmp"))
