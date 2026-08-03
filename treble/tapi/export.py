"""The bulk-export guard (spec §9.3, §8.5).

    A firm may pull the entire universe into its own warehouse; the
    architecture supports this explicitly. — §9.1

    Resolution and display work; bulk export of a CUSIP master does not.
    — §9.3

Both sentences are true at once, and this is where they meet. Bulk export is
a headline capability of an open workstation, and exactly two things must
not leave through it:

1. **Facts from a source whose terms do not permit redistribution.** TRACE
   forbids it outright. `dtcc-sdr` is the harder case: its terms could not
   be read at all, and DTCC sells a paid systematic-access product for the
   same data. Unverified is treated as restricted — putting the
   least-understood data in the first bulk export would be the worst
   possible order to find out.

2. **A licensed identifier database, as such.** ISIN and CUSIP arrive
   inside public regulatory filings and are stored and matched on, which is
   why looking one up works. Handing over the *mapping* as a dataset is a
   different act, and the one CUSIP Global Services licenses.

**Withheld, not silently dropped.** Every export reports what was removed
and which source it came from. A warehouse that received 90% of a universe
and believed it had all of it would compute portfolio coverage, index
weights and risk aggregates against a hole it could not see — and every one
of those numbers would look ordinary.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict

from treble.core.facts import Fact
from treble.core.provenance import ProvenanceId
from treble.ingest.registry import restricted_source_ids

#: Namespaces that are licensed identifier databases. Present in the store
#: because public filings publish them; not exportable as a mapping.
LICENSED_IDENTIFIER_NAMESPACES = frozenset({"cusip", "isin", "sedol"})


class ExportRefusedError(PermissionError):
    """The export is not permitted, and the message says which rule applies."""


class ExportResult(BaseModel):
    """What may leave, and what was held back."""

    model_config = ConfigDict(frozen=True)

    facts: tuple[Fact, ...]
    #: Count per source that was withheld. Present and empty when nothing
    #: was withheld, so a consumer always has the key to check rather than
    #: having to know the field might be absent.
    withheld_by_source: dict[str, int]
    #: Facts whose provenance could not be resolved. Withheld rather than
    #: exported: a fact whose source is unknown cannot be shown to come
    #: from an unrestricted one, and I1 says provenance is part of a value.
    withheld_unattributed: int

    @property
    def withheld_total(self) -> int:
        return sum(self.withheld_by_source.values()) + self.withheld_unattributed

    @property
    def is_complete(self) -> bool:
        """True when the export is the whole of what was asked for.

        A consumer that stores this alongside the data can tell later
        whether its copy was ever complete — which a row count cannot.
        """
        return self.withheld_total == 0


def check_selection(namespace: str) -> None:
    """Refuse a bulk pull of a licensed identifier namespace (§9.3).

    Scoped to the *selection*, not to the facts: this refuses "give me every
    CUSIP", while a screen resolving one bond by its CUSIP is untouched.
    That is precisely the line the spec draws — resolution and display work,
    bulk export of the master does not.
    """
    head = namespace.split(":", 1)[0].strip().lower()
    if head in LICENSED_IDENTIFIER_NAMESPACES:
        raise ExportRefusedError(
            f"bulk export of the {head!r} namespace is a licensed identifier database "
            f"({head.upper()} is licensed, unlike FIGI). Resolving or displaying a single "
            "instrument by that identifier is unaffected; handing over the mapping as a "
            "dataset is the act the licence covers (spec §9.3)"
        )


def filter_exportable(
    facts: Sequence[Fact],
    *,
    source_of: Callable[[ProvenanceId], str | None],
    restricted: frozenset[str] | None = None,
) -> ExportResult:
    """Remove facts whose source does not permit redistribution.

    ``source_of`` maps a provenance id to its source system — normally the
    store's provenance table. Injected rather than looked up here so the
    guard can be tested without a store, and so the rule stays a pure
    function of (facts, sources).

    ``restricted`` defaults to the discovered set. Overridable for tests,
    never in production: a caller that could pass an empty set could turn
    the guard off, so the *callers* in this codebase never pass it.
    """
    blocked = restricted_source_ids() if restricted is None else restricted
    allowed: list[Fact] = []
    withheld: dict[str, int] = {}
    unattributed = 0

    for fact in facts:
        source = source_of(fact.provenance_id)
        if source is None:
            unattributed += 1
            continue
        if source in blocked:
            withheld[source] = withheld.get(source, 0) + 1
            continue
        allowed.append(fact)

    return ExportResult(
        facts=tuple(allowed),
        withheld_by_source=withheld,
        withheld_unattributed=unattributed,
    )


__all__ = [
    "LICENSED_IDENTIFIER_NAMESPACES",
    "ExportRefusedError",
    "ExportResult",
    "check_selection",
    "filter_exportable",
]
