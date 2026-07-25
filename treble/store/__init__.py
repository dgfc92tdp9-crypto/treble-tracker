"""Store / HistoryStore / IngestLog protocols and their DuckDB+Parquet implementations.

Implements specification section §8.2, §8.6.
See docs/treble-tracker-spec.md and CLAUDE.md.
"""

from treble.store.duck import DuckStore, MissingProvenanceError
from treble.store.ingest_log import IngestLog, IngestLogEntry
from treble.store.payloads import PayloadHash, PayloadIntegrityError, PayloadStore, payload_hash
from treble.store.protocols import HistoryStore, IngestLogP, PayloadStoreP, Store

__all__ = [
    "DuckStore",
    "HistoryStore",
    "IngestLog",
    "IngestLogEntry",
    "IngestLogP",
    "MissingProvenanceError",
    "PayloadHash",
    "PayloadIntegrityError",
    "PayloadStore",
    "PayloadStoreP",
    "Store",
    "payload_hash",
]
