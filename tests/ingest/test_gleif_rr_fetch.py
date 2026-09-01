"""The RR adapter's fetch: take the delta, and refuse one that leaves a hole.

Offline. `httpx.Client` is replaced with one over a `MockTransport` serving
the recorded publishes index and zipped RR documents, so the decisions are
exercised end to end without the network.

The size assertions are here on purpose. The reason for this code is that
the full copy was 37 MB stored, every day, for 486,115 records of which
about 1,500 had changed — so a test suite that proved the logic correct
while quietly fetching the full file every time would be missing the point.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from treble.ingest import gleif as gleif_module
from treble.ingest.gleif import GleifRelationshipAdapter
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore

FIXTURES = Path(__file__).parent.parent / "fixtures" / "gleif"
PUBLISHES = json.loads((FIXTURES / "golden_publishes.json").read_text())
SAMPLE = (FIXTURES / "rr_sample.xml").read_bytes()

HEADER = b"""<?xml version="1.0" encoding="UTF-8"?>
<rr:RelationshipData xmlns:rr="http://www.gleif.org/data/schema/rr/2016">
  <rr:Header>
    <rr:ContentDate>{content}</rr:ContentDate>
    <rr:FileContent>{kind}</rr:FileContent>
{delta}  </rr:Header>
  <rr:RelationshipRecords/>
</rr:RelationshipData>
"""


def _document(*, content: str, delta_start: str | None) -> bytes:
    """An RR document with the header this code reads and no records.

    Records are irrelevant here — `parse` is covered against the real
    fixture in `test_gleif_relationship.py`, and what this file tests is
    which document gets downloaded.
    """
    delta = f"    <rr:DeltaStart>{delta_start}</rr:DeltaStart>\n" if delta_start else ""
    return (
        HEADER.replace(b"{content}", content.encode())
        .replace(b"{kind}", b"GLEIF_DELTA_PUBLISHED" if delta_start else b"GLEIF_FULL_PUBLISHED")
        .replace(b"{delta}", delta.encode())
    )


def _zipped(data: bytes, name: str = "rr.xml") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, data)
    return buffer.getvalue()


FULL = _document(content="2026-09-01T08:00:00Z", delta_start=None)
DELTA = _document(content="2026-09-01T08:00:00Z", delta_start="2026-08-31T00:00:00Z")
#: A delta whose window opens after what the store knows — the hole.
SHORT_DELTA = _document(content="2026-09-01T08:00:00Z", delta_start="2026-09-05T00:00:00Z")


class Recorder:
    """Serves the fixtures and remembers which URLs were asked for."""

    def __init__(self, *, delta: bytes = DELTA) -> None:
        self.urls: list[str] = []
        self._delta = delta

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.urls.append(url)
        if "publishes" in url:
            return httpx.Response(200, json=PUBLISHES)
        if "golden-copy.xml.zip" in url:
            return httpx.Response(200, content=_zipped(FULL))
        return httpx.Response(200, content=_zipped(self._delta))

    @property
    def downloaded(self) -> list[str]:
        return [u for u in self.urls if u.endswith(".zip")]


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch):
    def install(recorder: Recorder) -> Recorder:
        transport = httpx.MockTransport(recorder)
        # Bound before patching: `gleif_module.httpx` *is* the httpx module,
        # so a factory that called `httpx.Client` after the patch would call
        # itself.
        real_client = httpx.Client

        def client(*args: object, **kwargs: object) -> httpx.Client:
            return real_client(transport=transport)

        monkeypatch.setattr(gleif_module.httpx, "Client", client)
        return recorder

    return install


def _adapter(tmp_path: Path) -> GleifRelationshipAdapter:
    return GleifRelationshipAdapter(
        PayloadStore(tmp_path / "payloads"), IngestLog(tmp_path / "log.db")
    )


class TestTheFirstFetch:
    def test_an_empty_store_takes_the_full_copy(self, tmp_path: Path, serve) -> None:
        recorder = serve(Recorder())
        adapter = _adapter(tmp_path)
        list(adapter.run())
        assert len(recorder.downloaded) == 1
        assert recorder.downloaded[0].endswith("golden-copy.xml.zip")

    def test_it_records_how_current_the_store_now_is(self, tmp_path: Path, serve) -> None:
        serve(Recorder())
        adapter = _adapter(tmp_path)
        assert adapter.known_through() is None
        list(adapter.run())
        assert adapter.known_through() == datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


class TestTheSecondFetch:
    def test_it_takes_the_delta(self, tmp_path: Path, serve) -> None:
        """The whole point: 90 KB instead of 37 MB."""
        recorder = serve(Recorder())
        adapter = _adapter(tmp_path)
        list(adapter.run())
        list(adapter.run())
        assert len(recorder.downloaded) == 2
        assert recorder.downloaded[1].endswith("last-day.xml.zip")

    def test_the_payload_stored_is_the_delta(self, tmp_path: Path, serve) -> None:
        """Asserted through the payload store rather than the URL, because
        the URL is what was requested and this is what was kept."""
        serve(Recorder())
        adapter = _adapter(tmp_path)
        list(adapter.run())
        list(adapter.run())
        payloads = PayloadStore(tmp_path / "payloads")
        stored = payloads.get(IngestLog(tmp_path / "log.db").read()[-1].payload_hash)
        assert b"GLEIF_DELTA_PUBLISHED" in stored


class TestADeltaThatWouldLeaveAHole:
    def test_it_is_refused_and_the_full_copy_taken(self, tmp_path: Path, serve) -> None:
        """The failure the coverage check exists for. A delta beginning
        after what the store knows would lose the interval between —
        silently, because a short file and a quiet day look identical."""
        recorder = serve(Recorder(delta=SHORT_DELTA))
        adapter = _adapter(tmp_path)
        list(adapter.run())
        list(adapter.run())
        assert recorder.downloaded[-1].endswith("golden-copy.xml.zip")

    def test_the_short_delta_is_not_what_gets_stored(self, tmp_path: Path, serve) -> None:
        """Escalating but keeping the discarded file would log a payload
        that replay would then apply over the hole."""
        serve(Recorder(delta=SHORT_DELTA))
        adapter = _adapter(tmp_path)
        list(adapter.run())
        list(adapter.run())
        payloads = PayloadStore(tmp_path / "payloads")
        stored = payloads.get(IngestLog(tmp_path / "log.db").read()[-1].payload_hash)
        assert b"GLEIF_FULL_PUBLISHED" in stored

    def test_a_covering_delta_is_kept(self, tmp_path: Path, serve) -> None:
        """Proves the escalation above turns on coverage rather than always
        firing — otherwise both tests would pass with the delta path dead."""
        recorder = serve(Recorder())
        adapter = _adapter(tmp_path)
        list(adapter.run())
        list(adapter.run())
        assert recorder.downloaded[-1].endswith("last-day.xml.zip")


class TestKnownThrough:
    def test_a_missing_payload_falls_back_to_the_full_copy(self, tmp_path: Path, serve) -> None:
        """The safe direction. A log entry whose bytes have gone means the
        store's currency cannot be established, and a delta chosen against
        a guess is the one thing that must not happen."""
        recorder = serve(Recorder())
        adapter = _adapter(tmp_path)
        list(adapter.run())

        for path in (tmp_path / "payloads").rglob("*"):
            if path.is_file():
                path.unlink()

        assert adapter.known_through() is None
        list(adapter.run())
        assert recorder.downloaded[-1].endswith("golden-copy.xml.zip")

    def test_another_source_in_the_log_is_ignored(self, tmp_path: Path, serve) -> None:
        """The log is shared. Reading the newest entry regardless of source
        would size the gap against a FRED fetch."""
        serve(Recorder())
        adapter = _adapter(tmp_path)
        list(adapter.run())
        log = IngestLog(tmp_path / "log.db")
        log.append(
            source="fred",
            payload_hash="0" * 64,
            source_uri="https://example.invalid/fred",
            fetched_at=datetime(2026, 9, 2, tzinfo=UTC),
            parser_version="1",
        )
        assert adapter.known_through() == datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
