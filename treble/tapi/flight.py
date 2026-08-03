"""Arrow Flight — bulk columnar transport for TAPI (spec §8.3, §8.5).

    A firm may pull the entire universe into its own warehouse, and the
    architecture actively supports this. — §2.4

Flight is the transport that makes that sentence true rather than
aspirational. The store is already DuckDB over Parquet, so facts are
columnar before anything asks for them; Flight hands the same Arrow batches
straight to a client's warehouse with no row-by-row JSON in between.

**Every export goes through the guard** in `treble.tapi.export`, which is
the point of building this second. A bulk transport is where a
redistribution restriction stops being paperwork: TRACE forbids
redistribution outright, and `dtcc-sdr`'s terms could not be read at all.
Flight refuses a licensed identifier namespace outright and withholds
restricted-source facts from everything else, reporting both in the
response schema so a warehouse can tell whether its copy is complete.

**Bound to loopback, like the HTTP server, and for the same reason.** There
is no authentication here; §22.1's entitlement model — passkeys, per-user
entitlements, OIDC, audit logging — does not exist yet. A Flight server on
a routable interface would be an unauthenticated bulk data tap, so the
default host is `127.0.0.1` and the docstring on `serve` says what has to
exist before that changes.

**Not a second data path.** Flight reads through the same store handle the
rest of TAPI uses and applies the same point-in-time `as_of` semantics (I2).
A transport that could see facts the HTTP path could not would be a second
source of truth wearing a protocol as a disguise.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import pyarrow as pa
import pyarrow.flight as flight

from treble.core.identifiers import TUID
from treble.core.provenance import ProvenanceId
from treble.store.duck import DuckStore
from treble.tapi.export import ExportRefusedError, check_selection, filter_exportable

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8757

#: Arrow schema for a fact export. Explicit rather than inferred: a schema
#: derived from whatever rows happened to be in a batch would change shape
#: between two pulls of the same universe, and a warehouse would see a
#: column appear or vanish as a data event rather than a schema change.
FACT_SCHEMA = pa.schema(
    [
        pa.field("subject", pa.string(), nullable=False),
        pa.field("field", pa.string(), nullable=False),
        pa.field("value_num", pa.float64()),
        pa.field("value_text", pa.string()),
        pa.field("effective_from", pa.date32()),
        pa.field("effective_to", pa.date32()),
        pa.field("knowledge_from", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("provenance_id", pa.string(), nullable=False),
    ]
)


class FactExportServer(flight.FlightServerBase):  # type: ignore[misc]
    """Serves guarded fact exports over Arrow Flight.

    One descriptor shape: a JSON command naming a subject namespace and an
    `as_of`. Deliberately narrow — a general query surface over Flight would
    duplicate TQL, and two query languages disagreeing about the same store
    is how a workstation starts giving two answers.
    """

    def __init__(
        self, store: DuckStore, *, location: str = f"grpc://{DEFAULT_HOST}:{DEFAULT_PORT}"
    ) -> None:
        super().__init__(location)
        self._store = store
        self._location = location

    # -- descriptor handling -------------------------------------------

    @staticmethod
    def command(namespace: str, *, as_of: datetime | None = None) -> flight.FlightDescriptor:
        """Build the descriptor a client sends. Public so a client need not
        reconstruct the wire format by hand and drift from the server."""
        payload = {
            "namespace": namespace,
            "as_of": (as_of or datetime.now(UTC)).isoformat(),
        }
        return flight.FlightDescriptor.for_command(json.dumps(payload).encode())

    @staticmethod
    def _parse(descriptor: flight.FlightDescriptor) -> tuple[str, datetime]:
        try:
            payload = json.loads(bytes(descriptor.command).decode())
            namespace = str(payload["namespace"])
            as_of = datetime.fromisoformat(str(payload["as_of"]))
        except (ValueError, KeyError, TypeError) as error:
            raise flight.FlightServerError(
                f"malformed descriptor: expected JSON with 'namespace' and 'as_of' ({error})"
            ) from error
        if as_of.tzinfo is None:
            # DTZ everywhere else in this system; a naive as_of would make
            # the point-in-time cut depend on the server's timezone.
            raise flight.FlightServerError("as_of must be timezone-aware")
        return namespace, as_of

    # -- Flight surface -------------------------------------------------

    def get_flight_info(
        self, context: object, descriptor: flight.FlightDescriptor
    ) -> flight.FlightInfo:
        namespace, _ = self._parse(descriptor)
        self._guard_namespace(namespace)
        return flight.FlightInfo(
            FACT_SCHEMA,
            descriptor,
            [flight.FlightEndpoint(descriptor.command, [flight.Location(self._location)])],
            -1,  # row count unknown until the guard has run
            -1,
        )

    def do_get(self, context: object, ticket: flight.Ticket) -> flight.RecordBatchStream:
        descriptor = flight.FlightDescriptor.for_command(ticket.ticket)
        namespace, as_of = self._parse(descriptor)
        self._guard_namespace(namespace)
        table = self.export_table(namespace, as_of=as_of)
        return flight.RecordBatchStream(table)

    def list_flights(self, context: object, criteria: bytes) -> Iterator[flight.FlightInfo]:
        """Deliberately empty.

        Enumerating every namespace would be a catalogue of what this node
        holds, served without authentication. The client names what it
        wants; the server does not advertise.
        """
        return iter(())

    # -- the guarded export --------------------------------------------

    @staticmethod
    def _guard_namespace(namespace: str) -> None:
        try:
            check_selection(namespace)
        except ExportRefusedError as error:
            # Surfaced as a Flight error carrying the reason, not an empty
            # stream: a client that received zero rows would record "this
            # namespace is empty" and never learn it was refused.
            raise flight.FlightServerError(str(error)) from error

    def export_table(self, namespace: str, *, as_of: datetime) -> pa.Table:
        """Every exportable fact in a namespace, as an Arrow table.

        Separated from the Flight plumbing so the guard's behaviour can be
        tested without a server socket — and so the same guarded export can
        back other transports without either reimplementing it.
        """
        check_selection(namespace)
        prefix = namespace if namespace.endswith(":") else f"{namespace}:"
        facts = [
            fact
            for subject in self._store.subjects_with_prefix(prefix, as_of=as_of)
            for fact in self._store.subject_facts(TUID(str(subject)), as_of=as_of)
        ]
        result = filter_exportable(facts, source_of=self._source_of)
        return _to_table(result.facts, withheld=result.withheld_by_source)

    def _source_of(self, provenance_id: ProvenanceId) -> str | None:
        try:
            return self._store.provenance(provenance_id).source_system
        except (KeyError, LookupError):
            # Unknown source: `filter_exportable` withholds it. Returning a
            # placeholder string would let it pass as an unrestricted source.
            return None


def _to_table(facts: tuple[object, ...], *, withheld: dict[str, int]) -> pa.Table:
    """Arrow table with the withholding recorded in schema metadata.

    Metadata rather than a side channel: a warehouse that persists the table
    keeps the record of what was held back beside the data, so a later
    reader can still tell whether the copy was ever complete.
    """
    columns: dict[str, list[object]] = {name: [] for name in FACT_SCHEMA.names}
    for fact in facts:
        columns["subject"].append(str(fact.subject))  # type: ignore[attr-defined]
        columns["field"].append(fact.field)  # type: ignore[attr-defined]
        value = fact.value  # type: ignore[attr-defined]
        numeric = isinstance(value, int | float) and not isinstance(value, bool)
        columns["value_num"].append(float(value) if numeric else None)
        columns["value_text"].append(None if numeric else (None if value is None else str(value)))
        columns["effective_from"].append(fact.effective_from)  # type: ignore[attr-defined]
        columns["effective_to"].append(fact.effective_to)  # type: ignore[attr-defined]
        columns["knowledge_from"].append(fact.knowledge_from)  # type: ignore[attr-defined]
        columns["provenance_id"].append(str(fact.provenance_id))  # type: ignore[attr-defined]

    metadata = {
        b"treble.withheld": json.dumps(withheld, sort_keys=True).encode(),
        b"treble.complete": b"true" if not withheld else b"false",
    }
    return pa.Table.from_pydict(columns, schema=FACT_SCHEMA.with_metadata(metadata))


def serve(
    store: DuckStore, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
) -> None:  # pragma: no cover - process entry point
    """Run the Flight server.

    Loopback by default, and it must stay that way until §22.1 exists:
    there is no authentication, so a routable Flight server is an
    unauthenticated bulk data tap. The guard limits *what* leaves; it says
    nothing about *who* may ask.
    """
    FactExportServer(store, location=f"grpc://{host}:{port}").serve()


__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "FACT_SCHEMA", "FactExportServer", "serve"]
