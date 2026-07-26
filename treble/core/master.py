"""Security master and entity graph (spec §9.4, §9.5; WP7).

The seven ingest adapters each speak a different identifier dialect: EDGAR
uses CIK, OpenFIGI issues FIGI, GLEIF issues LEI, N-PORT and Treasury carry
CUSIP and ISIN. This module is where those become **one instrument** and
**one entity**.

Design, following the spec:

- **FIGI is the instrument key** (§9.3) — free, openly redistributable,
  never reused, so it survives ticker and name changes that silently
  corrupt ticker-keyed history.
- **LEI is the entity key** (§9.2), with the parent/subsidiary graph built
  from GLEIF relationship records (§9.5).
- **Licensed identifiers resolve but never bulk-export.** CUSIP and ISIN
  arrive inside public regulatory filings and are matched on; the
  redistribution guard (§9.3) blocks them leaving in bulk exports.

Resolution is *evidence-based*: every link is a stored Fact carrying its
own provenance (I1) and knowledge date (I2), so "why does this ISIN map to
that FIGI?" is answerable by `SPTR` like any other value, and a mapping
that was true last year stays true when queried as of last year.

Nothing here invents a link. Where two sources disagree, both mappings are
stored with their provenance and the conflict is reported — never silently
resolved by preferring one source (working agreement: no fabrication).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from treble.core.facts import Fact
from treble.core.identifiers import TUID

#: Fields the adapters emit that assert an identifier equivalence.
#: Each maps a subject key prefix to the identifier kind it resolves to.
MAPPING_FIELDS = {
    "openfigi:mapped:ID_ISIN": "isin",
    "openfigi:mapped:TICKER": "ticker",
    "openfigi:mapped:ID_CUSIP": "cusip",
    "nport:isin": "isin",
    "nport:cusip": "cusip",
    "nport:lei": "lei",
}

#: Identifier kinds that must never leave the system in a bulk export
#: (spec §9.3). Resolution and on-screen display are unaffected.
REDISTRIBUTION_RESTRICTED_KINDS = frozenset({"cusip", "isin"})


class IdentifierLink(BaseModel):
    """One evidence-carrying equivalence between two identifier keys."""

    model_config = ConfigDict(frozen=True)

    from_key: TUID
    to_key: TUID
    kind: str  # "isin", "cusip", "lei", "ticker"
    knowledge_from: datetime
    provenance_id: str

    @property
    def restricted(self) -> bool:
        return self.kind in REDISTRIBUTION_RESTRICTED_KINDS


class ConflictingLinkError(Exception):
    """Two sources assert incompatible mappings for the same identifier."""


def links_from_facts(facts: Iterable[Fact]) -> list[IdentifierLink]:
    """Extract identifier equivalences from stored facts.

    Pure function of the facts, so the master is reproducible by replay
    (I5) rather than accumulated as hidden state.
    """
    links: list[IdentifierLink] = []
    for fact in facts:
        kind = MAPPING_FIELDS.get(fact.field)
        if kind is None or fact.value in (None, ""):
            continue
        links.append(
            IdentifierLink(
                from_key=fact.subject,
                to_key=TUID(f"{kind}:{str(fact.value).upper()}"),
                kind=kind,
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return links


class FigiHierarchy(BaseModel):
    """The three FIGI levels (spec §9.3), as observed in real mappings.

    IBM's ISIN returns 200 rows spanning 83 *composite* FIGIs (one per
    country listing) but a single *share-class* FIGI — the global identity.
    So:

    - **share class** is authoritative for global instrument identity when
      present, which is what "cleanly solves same company, fourteen
      listings" means in practice;
    - **composite** is the fallback for instruments with no share class,
      which includes every bond (share class is an equity concept);
    - rows the source has not share-class-linked (3 of 200 for IBM, an OTC
      venue) are recorded but do **not** create a competing identity claim.
    """

    model_config = ConfigDict(frozen=True)

    share_class: dict[TUID, TUID]
    composite: dict[TUID, TUID]

    def canonical(self, figi: TUID) -> TUID:
        return self.share_class.get(figi) or self.composite.get(figi) or figi

    def has_share_class(self, figi: TUID) -> bool:
        return figi in self.share_class


def figi_hierarchy(facts: Iterable[Fact]) -> FigiHierarchy:
    """Build the level mapping from stored OpenFIGI facts."""
    share_class: dict[TUID, TUID] = {}
    composite: dict[TUID, TUID] = {}
    for fact in facts:
        if not fact.value:
            continue
        target = TUID(f"figi:{str(fact.value).upper()}")
        if fact.field == "openfigi:shareClassFIGI":
            share_class[fact.subject] = target
        elif fact.field == "openfigi:compositeFIGI":
            composite[fact.subject] = target
    return FigiHierarchy(share_class=share_class, composite=composite)


def _identity_candidates(
    links: Iterable[IdentifierLink], key: TUID, hierarchy: FigiHierarchy
) -> set[TUID]:
    claimants = [
        link.from_key
        for link in links
        if link.to_key == key and str(link.from_key).startswith("figi:")
    ]
    # Share-class-linked claimants win outright when any exist.
    linked = [figi for figi in claimants if hierarchy.has_share_class(figi)]
    return {hierarchy.canonical(figi) for figi in (linked or claimants)}


def resolve_instrument(
    links: Iterable[IdentifierLink],
    key: TUID,
    *,
    as_of: datetime,
    hierarchy: FigiHierarchy | None = None,
) -> TUID | None:
    """Resolve any identifier key to its canonical FIGI, as known at
    ``as_of`` (I2). Returns None when no evidence maps the key, or when
    the evidence is genuinely ambiguous — never a guess."""
    hierarchy = hierarchy or FigiHierarchy(share_class={}, composite={})
    visible = [link for link in links if link.knowledge_from <= as_of]
    if str(key).startswith("figi:"):
        return hierarchy.canonical(key)
    candidates = _identity_candidates(visible, key, hierarchy)
    return candidates.pop() if len(candidates) == 1 else None


def conflicts(
    links: Iterable[IdentifierLink], hierarchy: FigiHierarchy | None = None
) -> list[tuple[TUID, list[TUID]]]:
    """Identifier keys claimed by more than one canonical FIGI.

    Many venue-level FIGIs per identifier is the hierarchy working as
    designed. Two distinct *canonical* instruments claiming one ISIN is a
    real data problem, surfaced for review rather than silently resolved
    (spec §8.1.4: report disagreement, never pick a winner).
    """
    hierarchy = hierarchy or FigiHierarchy(share_class={}, composite={})
    link_list = list(links)
    keys = {link.to_key for link in link_list if str(link.from_key).startswith("figi:")}
    found: list[tuple[TUID, list[TUID]]] = []
    for key in keys:
        candidates = _identity_candidates(link_list, key, hierarchy)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found
