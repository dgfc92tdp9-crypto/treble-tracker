"""Arrow Flight bulk export (spec §8.3, §8.5).

Two things are being tested and only one of them is a transport. The
columnar plumbing is `pyarrow`'s and is not this project's to verify; what
is this project's is that **nothing leaves through it that should not**, and
that a client can tell when something was held back.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.export import ExportRefusedError
from treble.tapi.flight import DEFAULT_HOST, FACT_SCHEMA, FactExportServer

AS_OF = datetime(2026, 8, 3, 18, 0, tzinfo=UTC)
KNOWN = datetime(2026, 8, 1, 6, 0, tzinfo=UTC)
DAY = date(2026, 7, 31)


def _write(store: DuckStore, source: str, subjects: list[str]) -> None:
    record = Provenance(
        source_system=source,
        source_uri=f"https://example.invalid/{source}",
        retrieved_at=KNOWN,
        method=ExtractionMethod.API,
        extractor_version="1",
        payload_hash=source.ljust(64, "0")[:64],
    )
    store.write_provenance([record])
    store.write_facts(
        [
            Fact(
                subject=subject,
                field="PX_LAST",
                value=100.0 + i,
                effective_from=DAY,
                effective_to=DAY,
                knowledge_from=KNOWN,
                provenance_id=record.id,
            )
            for i, subject in enumerate(subjects)
        ]
    )


@pytest.fixture
def server(tmp_path: Path) -> FactExportServer:
    store = DuckStore(tmp_path / "t.db")
    _write(store, "fred", ["fred:DGS10", "fred:DGS2"])
    _write(store, "dtcc-sdr", ["swap:USD-SOFR-OIS:10Y", "swap:USD-SOFR-OIS:2Y"])
    return FactExportServer(store, location="grpc://127.0.0.1:0")


class TestTheGuardRunsOnTheTransport:
    def test_an_open_namespace_exports(self, server: FactExportServer) -> None:
        table = server.export_table("fred", as_of=AS_OF)
        assert table.num_rows == 2
        assert table.schema.metadata[b"treble.complete"] == b"true"

    def test_a_restricted_source_is_withheld(self, server: FactExportServer) -> None:
        """DTCC's terms could not be read, and DTCC sells a paid systematic-
        access product for the same data. A bulk transport is where that
        stops being paperwork."""
        table = server.export_table("swap", as_of=AS_OF)
        assert table.num_rows == 0
        assert table.schema.metadata[b"treble.complete"] == b"false"
        assert b"dtcc-sdr" in table.schema.metadata[b"treble.withheld"]

    def test_a_licensed_namespace_is_refused_not_emptied(self, server: FactExportServer) -> None:
        """Refused, so a client learns it was refused. Returning zero rows
        would have it record 'this namespace is empty' and never find out."""
        for namespace in ("cusip", "isin"):
            with pytest.raises(ExportRefusedError, match="licensed identifier"):
                server.export_table(namespace, as_of=AS_OF)

    def test_the_withholding_travels_with_the_data(self, server: FactExportServer) -> None:
        """Schema metadata rather than a side channel: a warehouse that
        persists the table keeps the record of what was held back beside it,
        so a later reader can still tell whether the copy was complete."""
        table = server.export_table("swap", as_of=AS_OF)
        assert set(table.schema.metadata) >= {b"treble.withheld", b"treble.complete"}


class TestTheSchemaIsFixed:
    def test_the_schema_does_not_depend_on_the_rows(self, server: FactExportServer) -> None:
        """A schema inferred from whatever happened to be in a batch would
        change shape between two pulls of the same universe, and a warehouse
        would see a column appear or vanish as a data event."""
        populated = server.export_table("fred", as_of=AS_OF)
        empty = server.export_table("swap", as_of=AS_OF)
        assert populated.schema.names == empty.schema.names == FACT_SCHEMA.names

    def test_numeric_and_text_values_go_to_separate_columns(self, server: FactExportServer) -> None:
        table = server.export_table("fred", as_of=AS_OF)
        assert table.column("value_num").to_pylist() == [100.0, 101.0]
        assert set(table.column("value_text").to_pylist()) == {None}

    def test_provenance_travels_with_every_row(self, server: FactExportServer) -> None:
        """I1: provenance is part of a value, so an export that dropped it
        would hand over facts that could never be traced again."""
        table = server.export_table("fred", as_of=AS_OF)
        assert all(table.column("provenance_id").to_pylist())


class TestPointInTime:
    def test_export_respects_as_of(self, server: FactExportServer) -> None:
        """I2. A transport that could see facts the screen path could not
        would be a second source of truth wearing a protocol as a
        disguise."""
        before = server.export_table("fred", as_of=datetime(2026, 7, 30, tzinfo=UTC))
        assert before.num_rows == 0

    def test_a_naive_as_of_is_refused(self, server: FactExportServer) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            server.export_table("fred", as_of=datetime(2026, 8, 3))  # noqa: DTZ001


class TestDescriptors:
    def test_the_command_round_trips(self) -> None:
        """The server publishes the descriptor builder so a client does not
        reconstruct the wire format by hand and drift from it."""
        descriptor = FactExportServer.command("fred", as_of=AS_OF)
        namespace, as_of = FactExportServer._parse(descriptor)
        assert (namespace, as_of) == ("fred", AS_OF)

    def test_a_malformed_descriptor_is_refused(self) -> None:
        import pyarrow.flight as flight

        bad = flight.FlightDescriptor.for_command(b"not json")
        with pytest.raises(flight.FlightServerError, match="malformed descriptor"):
            FactExportServer._parse(bad)

    def test_a_naive_as_of_in_a_descriptor_is_refused(self) -> None:
        import json

        import pyarrow.flight as flight

        naive = flight.FlightDescriptor.for_command(
            json.dumps({"namespace": "fred", "as_of": "2026-08-03T12:00:00"}).encode()
        )
        with pytest.raises(flight.FlightServerError, match="timezone-aware"):
            FactExportServer._parse(naive)


class TestNoNetworkSurfaceByDefault:
    def test_the_default_host_is_loopback(self) -> None:
        """There is no authentication here — §22.1's entitlement model does
        not exist — so a routable Flight server would be an unauthenticated
        bulk data tap."""
        assert DEFAULT_HOST == "127.0.0.1"

    def test_the_server_does_not_advertise_its_contents(self, server: FactExportServer) -> None:
        """Enumerating namespaces would be a catalogue of what this node
        holds, served without authentication."""
        assert list(server.list_flights(None, b"")) == []
