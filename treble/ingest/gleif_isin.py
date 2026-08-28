"""GLEIF's ISIN-to-LEI mapping (spec §13.2, entity resolution).

The authoritative link between an instrument and the legal entity that
issued it. GLEIF publishes it daily as a zipped two-column CSV under CC0 —
no key, no licence to withdraw, `mapping.gleif.org/robots.txt` is a bare
`Disallow:` with no path — and it is the registry's own record rather than
anyone's report of it.

**This is not redundant with N-PORT, and the difference is the point.**
N-PORT already carries an issuer LEI for every bond in this store, but that
LEI is *filer-reported*: a fund administrator typing what it believes the
issuer to be. Measured against GLEIF on 2026-08-10, 1,148 of 1,163
overlapping bonds agreed and **15 did not** — 1.3%. The disagreements are
not random noise. Three of them are bonds a filer attributed to Deutsche
Bank AG's LEI while GLEIF assigns them to a different entity, which is the
classic shape of a filer naming the parent or guarantor rather than the
subsidiary that actually issued the paper.

That 1.3% matters more than it sounds. An issuer curve is fitted across one
entity's outstanding debt, so a bond attributed to the parent is a bond
fitted onto the wrong credit — and the fit still succeeds, still looks
smooth, and still produces a rich/cheap call.

**Both facts are kept.** GLEIF's is written under its own field rather than
overwriting N-PORT's, because a disagreement between a registry and a filer
is evidence about the filing, and an ingest that silently replaced one with
the other would destroy exactly the signal worth having (I1, I2).

**Only requested ISINs are parsed.** The file is 9.1 million rows and 310MB
uncompressed against a store holding 1,861 bonds. Ingesting all of it would
add ten million facts to answer questions about two thousand instruments.
The raw payload is still stored whole, so I5 replay reproduces the parse
exactly; it is the *fact* set that is scoped, not the evidence.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC
from typing import Any, Final

import httpx

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.ingest.base import (
    ParsedBatch,
    RawPayload,
    SourceAdapter,
    SourceMeta,
    utcnow,
)
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore

#: Lists every published mapping file, newest first.
INDEX_URL: Final = "https://mapping.gleif.org/api/v2/isin-lei"

#: The field GLEIF's link is written under. Deliberately *not* `nport:lei`:
#: see the module docstring — the two disagree on 1.3% of bonds and the
#: disagreement is the useful part.
FIELD: Final = "gleif:lei"


#: Where the entity itself lives, so a caller can walk into the GLEIF
#: relationship graph from an instrument.
def lei_subject(lei: str) -> TUID:
    """`5493...` -> `lei:5493...`, the subject the RR graph is keyed on."""
    return TUID(f"lei:{lei.upper()}")


class MappingUnavailableError(RuntimeError):
    """GLEIF published no usable mapping file.

    Raised rather than returning nothing. An empty listing and a listing
    whose newest entry failed processing are both "no data today", and a
    silent empty parse would log a successful fetch and mark the source
    fresh while writing nothing at all.
    """


class GleifIsinLeiAdapter(SourceAdapter):
    """Maps the ISINs a caller names to their registered issuer LEI."""

    meta = SourceMeta(
        source_id="gleif-isin",
        description="GLEIF ISIN-to-LEI mapping, filtered to the requested instruments",
        licence="CC0 — GLEIF data is fully open",
        redistribution_restricted=False,
        rate_limit_per_second=0.5,
        # Republished every day, including weekends.
        expected_cadence_days=1.0,
    )
    parser_version = "1"

    def __init__(
        self,
        payloads: PayloadStore,
        log: IngestLog,
        *,
        isins: Iterable[str],
        timeout: float = 300.0,
    ) -> None:
        super().__init__(payloads, log)
        self._isins = frozenset(i.strip().upper() for i in isins if i.strip())
        self._timeout = timeout

    def parse_config(self) -> dict[str, Any]:
        """The ISIN filter. Unlike `edgar-bulk`'s there is no "None means
        all" escape, so a replay without it produced *zero* facts from a
        payload holding millions of rows."""
        return {"isins": sorted(self._isins)}

    def apply_parse_config(self, config: Mapping[str, Any]) -> None:
        self._isins = frozenset(str(i).strip().upper() for i in config.get("isins", ()))

    def fetch(self) -> Iterator[RawPayload]:
        with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
            index = client.get(INDEX_URL)
            index.raise_for_status()
            entries = json.loads(index.content).get("data") or []
            newest = next(
                (
                    entry
                    for entry in entries
                    if entry.get("attributes", {}).get("processed")
                    and entry.get("attributes", {}).get("valid")
                ),
                None,
            )
            if newest is None:
                raise MappingUnavailableError(
                    f"{INDEX_URL} listed {len(entries)} file(s), none processed and valid"
                )
            link = newest["attributes"]["downloadLink"]
            body = client.get(link)
            body.raise_for_status()
            yield RawPayload(data=body.content, source_uri=link, fetched_at=utcnow())

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        record = Provenance(
            source_system=self.meta.source_id,
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.BULK_FILE,
            extractor_version=self.parser_version,
            payload_hash=str(payload_hash),
        )
        when = payload.fetched_at.astimezone(UTC).date()
        facts: list[Fact] = []
        seen: set[str] = set()
        for lei, isin in _rows(payload.data):
            if isin not in self._isins or isin in seen:
                # First mapping wins. GLEIF occasionally lists an ISIN twice
                # while a transfer is in flight; taking the last would make
                # the answer depend on file order, which is not a property
                # anyone promised.
                continue
            seen.add(isin)
            facts.append(
                Fact(
                    subject=str(TUID(f"isin:{isin}")),
                    field=FIELD,
                    value=lei,
                    effective_from=when,
                    effective_to=when,
                    knowledge_from=payload.fetched_at,
                    provenance_id=record.id,
                )
            )
        return ParsedBatch(provenance=(record,), facts=tuple(facts))


def _rows(data: bytes) -> Iterator[tuple[str, str]]:
    """Stream `(lei, isin)` from the zipped CSV.

    Streamed rather than read whole: the archive expands to 310MB, and
    materialising that to pick out a couple of thousand rows would make the
    adapter's memory a function of GLEIF's publishing rather than of the
    universe being tracked.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise MappingUnavailableError(f"no CSV inside the archive: {archive.namelist()}")
        with archive.open(names[0]) as handle:
            text = io.TextIOWrapper(handle, encoding="utf-8")
            header = text.readline().strip().upper()
            if "LEI" not in header or "ISIN" not in header:
                raise MappingUnavailableError(
                    f"unexpected header {header!r}; this parser reads GLEIF's LEI,ISIN pairs "
                    "and a changed column order would silently swap the two identifiers"
                )
            lei_first = header.index("LEI") < header.index("ISIN")
            for line in text:
                first, _, second = line.partition(",")
                first, second = first.strip(), second.strip()
                if not first or not second:
                    continue
                yield (first, second) if lei_first else (second, first)


def disagreements(
    gleif: dict[str, str], reported: dict[str, str]
) -> tuple[tuple[str, str, str], ...]:
    """ISINs where the registry and the filer name different issuers.

    Returned rather than logged. A filer attributing a subsidiary's bond to
    its parent puts that bond on the wrong issuer curve, and the fit
    succeeds regardless — so this is the only place the discrepancy becomes
    visible.
    """
    return tuple(
        (isin, reported[isin], lei)
        for isin, lei in sorted(gleif.items())
        if isin in reported and reported[isin] != lei
    )


__all__ = [
    "FIELD",
    "INDEX_URL",
    "GleifIsinLeiAdapter",
    "MappingUnavailableError",
    "disagreements",
    "lei_subject",
]
