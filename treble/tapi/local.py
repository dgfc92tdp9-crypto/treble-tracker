"""Local TAPI — the in-process implementation of the data path (spec §8.3).

Invariant I7: every screen, export and client feature reads through TAPI.
This is the local-only transport (spec §2.4, §23.3); the gRPC/Arrow Flight
server transport arrives at Phase 2 behind the same interface, so nothing
above this layer changes when it does.

Two responsibilities:

- **Resolve a security reference to a subject key.** `IBM US Equity` must
  become the storage key the facts were written under. The mapping comes
  from EDGAR's own company index, so it is source-derived, not a hand-kept
  list that would drift.
- **Read fields point-in-time.** `as_of` is required all the way down (I2);
  values carry their provenance id (I1) and staleness so the presentation
  layer can honour the display conventions (§6.3) without knowing anything
  about storage.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from treble.core.identifiers import TUID, SecurityQuery, YellowKey
from treble.store.duck import DuckStore
from treble.tapi.fields import FIELDS, FieldDictionary
from treble.tapi.types import FieldResult

#: A value older than this is displayed as stale (§6.3 mandates that any
#: value known not to be current is visually distinguished). Fundamentals
#: are quarterly, so the window is generous; the ticker plant tightens this
#: dramatically at Phase 2.
DEFAULT_STALE_AFTER = timedelta(days=120)


class SecurityNotFoundError(KeyError):
    """No subject key could be resolved for the reference."""


class TickerIndex:
    """ticker -> CIK, built from EDGAR's published company index.

    Source-derived rather than hand-maintained: the same payload the
    population runner uses for discovery also supplies this mapping, so the
    two cannot disagree.
    """

    def __init__(self, mapping: dict[str, int]) -> None:
        self._by_ticker = {k.upper(): v for k, v in mapping.items()}

    @classmethod
    def from_company_index(cls, payload: bytes) -> TickerIndex:
        doc = json.loads(payload)
        rows = doc.values() if isinstance(doc, dict) else doc
        mapping: dict[str, int] = {}
        for row in rows:
            if isinstance(row, dict) and row.get("ticker") and row.get("cik_str"):
                mapping[str(row["ticker"])] = int(row["cik_str"])
        return cls(mapping)

    def cik(self, ticker: str) -> int | None:
        return self._by_ticker.get(ticker.upper())

    def __len__(self) -> int:
        return len(self._by_ticker)


class LocalTapi:
    """In-process TAPI over the local store."""

    def __init__(
        self,
        store: DuckStore,
        *,
        tickers: TickerIndex | None = None,
        fields: FieldDictionary | None = None,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
    ) -> None:
        self._store = store
        self._tickers = tickers
        self._fields = fields or FIELDS
        self._stale_after = stale_after

    # -- resolution ----------------------------------------------------

    def resolve(self, security: SecurityQuery) -> TUID:
        """Security reference -> storage subject key.

        Equities resolve through the EDGAR company index to a CIK, which is
        the key the fundamentals were written under. Other namespaces get
        their own resolution as their data lands (bonds by ISIN/CUSIP from
        N-PORT, macro series by FRED id).
        """
        if security.key in (YellowKey.EQUITY, YellowKey.PFD):
            if self._tickers is None:
                raise SecurityNotFoundError(
                    "no ticker index loaded; equity resolution needs EDGAR's "
                    "company index (see TickerIndex.from_company_index)"
                )
            cik = self._tickers.cik(security.ticker)
            if cik is None:
                raise SecurityNotFoundError(
                    f"{security.display()!r}: ticker not in EDGAR's company index"
                )
            return TUID(f"cik:{cik:010d}")
        if security.key == YellowKey.INDEX and security.venue is None:
            # Macro series are addressable as tickers (spec §7.4).
            return TUID(f"fred:{security.ticker.upper()}")
        raise SecurityNotFoundError(
            f"{security.display()!r}: no resolution for namespace "
            f"{security.key.value!r} yet — data for it has not been ingested"
        )

    # -- reads (I2: as_of required) ------------------------------------

    def field(
        self,
        security: SecurityQuery | None,
        mnemonic: str,
        overrides: dict[str, str],
        *,
        as_of: datetime,
    ) -> FieldResult:
        """One field value, point-in-time, with provenance and staleness."""
        definition = self._fields.get(mnemonic)  # raises on unknown mnemonic

        if definition.model_derived:
            # Model outputs are computed by the analytics layer and carry an
            # I3 envelope. Wiring that through is the YAS screen's work; a
            # fabricated number here would be exactly the failure mode the
            # spec calls the worst in this domain.
            raise NotImplementedError(
                f"{mnemonic!r} is model-derived ({definition.model_id}); "
                "analytics wiring lands with the YAS screen (spec §10.1)"
            )
        if definition.stored_field is None or security is None:
            return FieldResult(value=None)

        facts = self._store.read(self.resolve(security), definition.stored_field, as_of=as_of)
        if not facts:
            # Genuinely absent, not an error: null with no provenance, which
            # the renderer shows as an em dash rather than a zero.
            return FieldResult(value=None)

        latest = max(facts, key=lambda f: f.effective_from)
        age = as_of.date() - (latest.effective_to or latest.effective_from)
        return FieldResult(
            value=latest.value,
            provenance_id=latest.provenance_id,
            stale=age > self._stale_after,
            model_derived=False,
        )

    def series(
        self, security: SecurityQuery | None, binding: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """A time series for a graphical pane: (effective date, value) rows."""
        if security is None:
            return ()
        definition = self._fields.get(binding)
        if definition.stored_field is None:
            return ()
        facts = self._store.history(self.resolve(security), definition.stored_field, as_of=as_of)
        return tuple(
            (f.effective_from.isoformat(), f.value)  # type: ignore[misc]
            for f in sorted(facts, key=lambda f: f.effective_from)
        )

    # -- dictionary ----------------------------------------------------

    def search_fields(self, query: str, *, limit: int = 50):  # type: ignore[no-untyped-def]
        """Backs `FLDS`."""
        return self._fields.search(query, limit=limit)


def utc_now() -> datetime:
    """Default `as_of` at the TAPI boundary (I2: never inside the store)."""
    return datetime.now(UTC)
