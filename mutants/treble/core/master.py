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


from mutmut.mutation.trampoline import wrap_in_trampoline as _mutmut_mutated, MutantDict


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
mutants_x_links_from_facts__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_links_from_facts__mutmut)
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


def x_links_from_facts__mutmut_orig(facts: Iterable[Fact]) -> list[IdentifierLink]:
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


def x_links_from_facts__mutmut_1(facts: Iterable[Fact]) -> list[IdentifierLink]:
    """Extract identifier equivalences from stored facts.

    Pure function of the facts, so the master is reproducible by replay
    (I5) rather than accumulated as hidden state.
    """
    links: list[IdentifierLink] = None
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


def x_links_from_facts__mutmut_2(facts: Iterable[Fact]) -> list[IdentifierLink]:
    """Extract identifier equivalences from stored facts.

    Pure function of the facts, so the master is reproducible by replay
    (I5) rather than accumulated as hidden state.
    """
    links: list[IdentifierLink] = []
    for fact in facts:
        kind = None
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


def x_links_from_facts__mutmut_3(facts: Iterable[Fact]) -> list[IdentifierLink]:
    """Extract identifier equivalences from stored facts.

    Pure function of the facts, so the master is reproducible by replay
    (I5) rather than accumulated as hidden state.
    """
    links: list[IdentifierLink] = []
    for fact in facts:
        kind = MAPPING_FIELDS.get(None)
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


def x_links_from_facts__mutmut_4(facts: Iterable[Fact]) -> list[IdentifierLink]:
    """Extract identifier equivalences from stored facts.

    Pure function of the facts, so the master is reproducible by replay
    (I5) rather than accumulated as hidden state.
    """
    links: list[IdentifierLink] = []
    for fact in facts:
        kind = MAPPING_FIELDS.get(fact.field)
        if kind is None and fact.value in (None, ""):
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


def x_links_from_facts__mutmut_5(facts: Iterable[Fact]) -> list[IdentifierLink]:
    """Extract identifier equivalences from stored facts.

    Pure function of the facts, so the master is reproducible by replay
    (I5) rather than accumulated as hidden state.
    """
    links: list[IdentifierLink] = []
    for fact in facts:
        kind = MAPPING_FIELDS.get(fact.field)
        if kind is not None or fact.value in (None, ""):
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


def x_links_from_facts__mutmut_6(facts: Iterable[Fact]) -> list[IdentifierLink]:
    """Extract identifier equivalences from stored facts.

    Pure function of the facts, so the master is reproducible by replay
    (I5) rather than accumulated as hidden state.
    """
    links: list[IdentifierLink] = []
    for fact in facts:
        kind = MAPPING_FIELDS.get(fact.field)
        if kind is None or fact.value not in (None, ""):
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


def x_links_from_facts__mutmut_7(facts: Iterable[Fact]) -> list[IdentifierLink]:
    """Extract identifier equivalences from stored facts.

    Pure function of the facts, so the master is reproducible by replay
    (I5) rather than accumulated as hidden state.
    """
    links: list[IdentifierLink] = []
    for fact in facts:
        kind = MAPPING_FIELDS.get(fact.field)
        if kind is None or fact.value in (None, "XXXX"):
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


def x_links_from_facts__mutmut_8(facts: Iterable[Fact]) -> list[IdentifierLink]:
    """Extract identifier equivalences from stored facts.

    Pure function of the facts, so the master is reproducible by replay
    (I5) rather than accumulated as hidden state.
    """
    links: list[IdentifierLink] = []
    for fact in facts:
        kind = MAPPING_FIELDS.get(fact.field)
        if kind is None or fact.value in (None, ""):
            break
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


def x_links_from_facts__mutmut_9(facts: Iterable[Fact]) -> list[IdentifierLink]:
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
            None
        )
    return links


def x_links_from_facts__mutmut_10(facts: Iterable[Fact]) -> list[IdentifierLink]:
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
                from_key=None,
                to_key=TUID(f"{kind}:{str(fact.value).upper()}"),
                kind=kind,
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return links


def x_links_from_facts__mutmut_11(facts: Iterable[Fact]) -> list[IdentifierLink]:
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
                to_key=None,
                kind=kind,
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return links


def x_links_from_facts__mutmut_12(facts: Iterable[Fact]) -> list[IdentifierLink]:
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
                kind=None,
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return links


def x_links_from_facts__mutmut_13(facts: Iterable[Fact]) -> list[IdentifierLink]:
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
                knowledge_from=None,
                provenance_id=str(fact.provenance_id),
            )
        )
    return links


def x_links_from_facts__mutmut_14(facts: Iterable[Fact]) -> list[IdentifierLink]:
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
                provenance_id=None,
            )
        )
    return links


def x_links_from_facts__mutmut_15(facts: Iterable[Fact]) -> list[IdentifierLink]:
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
                to_key=TUID(f"{kind}:{str(fact.value).upper()}"),
                kind=kind,
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return links


def x_links_from_facts__mutmut_16(facts: Iterable[Fact]) -> list[IdentifierLink]:
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
                kind=kind,
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return links


def x_links_from_facts__mutmut_17(facts: Iterable[Fact]) -> list[IdentifierLink]:
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
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return links


def x_links_from_facts__mutmut_18(facts: Iterable[Fact]) -> list[IdentifierLink]:
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
                provenance_id=str(fact.provenance_id),
            )
        )
    return links


def x_links_from_facts__mutmut_19(facts: Iterable[Fact]) -> list[IdentifierLink]:
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
                )
        )
    return links


def x_links_from_facts__mutmut_20(facts: Iterable[Fact]) -> list[IdentifierLink]:
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
                to_key=TUID(None),
                kind=kind,
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return links


def x_links_from_facts__mutmut_21(facts: Iterable[Fact]) -> list[IdentifierLink]:
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
                to_key=TUID(f"{kind}:{str(fact.value).lower()}"),
                kind=kind,
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return links


def x_links_from_facts__mutmut_22(facts: Iterable[Fact]) -> list[IdentifierLink]:
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
                to_key=TUID(f"{kind}:{str(None).upper()}"),
                kind=kind,
                knowledge_from=fact.knowledge_from,
                provenance_id=str(fact.provenance_id),
            )
        )
    return links


def x_links_from_facts__mutmut_23(facts: Iterable[Fact]) -> list[IdentifierLink]:
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
                provenance_id=str(None),
            )
        )
    return links

mutants_x_links_from_facts__mutmut['_mutmut_orig'] = x_links_from_facts__mutmut_orig # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_1'] = x_links_from_facts__mutmut_1 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_2'] = x_links_from_facts__mutmut_2 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_3'] = x_links_from_facts__mutmut_3 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_4'] = x_links_from_facts__mutmut_4 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_5'] = x_links_from_facts__mutmut_5 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_6'] = x_links_from_facts__mutmut_6 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_7'] = x_links_from_facts__mutmut_7 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_8'] = x_links_from_facts__mutmut_8 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_9'] = x_links_from_facts__mutmut_9 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_10'] = x_links_from_facts__mutmut_10 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_11'] = x_links_from_facts__mutmut_11 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_12'] = x_links_from_facts__mutmut_12 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_13'] = x_links_from_facts__mutmut_13 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_14'] = x_links_from_facts__mutmut_14 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_15'] = x_links_from_facts__mutmut_15 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_16'] = x_links_from_facts__mutmut_16 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_17'] = x_links_from_facts__mutmut_17 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_18'] = x_links_from_facts__mutmut_18 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_19'] = x_links_from_facts__mutmut_19 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_20'] = x_links_from_facts__mutmut_20 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_21'] = x_links_from_facts__mutmut_21 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_22'] = x_links_from_facts__mutmut_22 # type: ignore # mutmut generated
mutants_x_links_from_facts__mutmut['x_links_from_facts__mutmut_23'] = x_links_from_facts__mutmut_23 # type: ignore # mutmut generated
mutants_xǁFigiHierarchyǁcanonical__mutmut: MutantDict = {}  # type: ignore
mutants_xǁFigiHierarchyǁhas_share_class__mutmut: MutantDict = {}  # type: ignore


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

    @_mutmut_mutated(mutants_xǁFigiHierarchyǁcanonical__mutmut)
    def canonical(self, figi: TUID) -> TUID:
        return self.share_class.get(figi) or self.composite.get(figi) or figi

    def xǁFigiHierarchyǁcanonical__mutmut_orig(self, figi: TUID) -> TUID:
        return self.share_class.get(figi) or self.composite.get(figi) or figi

    def xǁFigiHierarchyǁcanonical__mutmut_1(self, figi: TUID) -> TUID:
        return self.share_class.get(figi) or self.composite.get(figi) and figi

    def xǁFigiHierarchyǁcanonical__mutmut_2(self, figi: TUID) -> TUID:
        return self.share_class.get(figi) and self.composite.get(figi) or figi

    def xǁFigiHierarchyǁcanonical__mutmut_3(self, figi: TUID) -> TUID:
        return self.share_class.get(None) or self.composite.get(figi) or figi

    def xǁFigiHierarchyǁcanonical__mutmut_4(self, figi: TUID) -> TUID:
        return self.share_class.get(figi) or self.composite.get(None) or figi

    @_mutmut_mutated(mutants_xǁFigiHierarchyǁhas_share_class__mutmut)
    def has_share_class(self, figi: TUID) -> bool:
        return figi in self.share_class

    def xǁFigiHierarchyǁhas_share_class__mutmut_orig(self, figi: TUID) -> bool:
        return figi in self.share_class

    def xǁFigiHierarchyǁhas_share_class__mutmut_1(self, figi: TUID) -> bool:
        return figi not in self.share_class

mutants_xǁFigiHierarchyǁcanonical__mutmut['_mutmut_orig'] = FigiHierarchy.xǁFigiHierarchyǁcanonical__mutmut_orig # type: ignore # mutmut generated
mutants_xǁFigiHierarchyǁcanonical__mutmut['xǁFigiHierarchyǁcanonical__mutmut_1'] = FigiHierarchy.xǁFigiHierarchyǁcanonical__mutmut_1 # type: ignore # mutmut generated
mutants_xǁFigiHierarchyǁcanonical__mutmut['xǁFigiHierarchyǁcanonical__mutmut_2'] = FigiHierarchy.xǁFigiHierarchyǁcanonical__mutmut_2 # type: ignore # mutmut generated
mutants_xǁFigiHierarchyǁcanonical__mutmut['xǁFigiHierarchyǁcanonical__mutmut_3'] = FigiHierarchy.xǁFigiHierarchyǁcanonical__mutmut_3 # type: ignore # mutmut generated
mutants_xǁFigiHierarchyǁcanonical__mutmut['xǁFigiHierarchyǁcanonical__mutmut_4'] = FigiHierarchy.xǁFigiHierarchyǁcanonical__mutmut_4 # type: ignore # mutmut generated

mutants_xǁFigiHierarchyǁhas_share_class__mutmut['_mutmut_orig'] = FigiHierarchy.xǁFigiHierarchyǁhas_share_class__mutmut_orig # type: ignore # mutmut generated
mutants_xǁFigiHierarchyǁhas_share_class__mutmut['xǁFigiHierarchyǁhas_share_class__mutmut_1'] = FigiHierarchy.xǁFigiHierarchyǁhas_share_class__mutmut_1 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_figi_hierarchy__mutmut)
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


def x_figi_hierarchy__mutmut_orig(facts: Iterable[Fact]) -> FigiHierarchy:
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


def x_figi_hierarchy__mutmut_1(facts: Iterable[Fact]) -> FigiHierarchy:
    """Build the level mapping from stored OpenFIGI facts."""
    share_class: dict[TUID, TUID] = None
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


def x_figi_hierarchy__mutmut_2(facts: Iterable[Fact]) -> FigiHierarchy:
    """Build the level mapping from stored OpenFIGI facts."""
    share_class: dict[TUID, TUID] = {}
    composite: dict[TUID, TUID] = None
    for fact in facts:
        if not fact.value:
            continue
        target = TUID(f"figi:{str(fact.value).upper()}")
        if fact.field == "openfigi:shareClassFIGI":
            share_class[fact.subject] = target
        elif fact.field == "openfigi:compositeFIGI":
            composite[fact.subject] = target
    return FigiHierarchy(share_class=share_class, composite=composite)


def x_figi_hierarchy__mutmut_3(facts: Iterable[Fact]) -> FigiHierarchy:
    """Build the level mapping from stored OpenFIGI facts."""
    share_class: dict[TUID, TUID] = {}
    composite: dict[TUID, TUID] = {}
    for fact in facts:
        if fact.value:
            continue
        target = TUID(f"figi:{str(fact.value).upper()}")
        if fact.field == "openfigi:shareClassFIGI":
            share_class[fact.subject] = target
        elif fact.field == "openfigi:compositeFIGI":
            composite[fact.subject] = target
    return FigiHierarchy(share_class=share_class, composite=composite)


def x_figi_hierarchy__mutmut_4(facts: Iterable[Fact]) -> FigiHierarchy:
    """Build the level mapping from stored OpenFIGI facts."""
    share_class: dict[TUID, TUID] = {}
    composite: dict[TUID, TUID] = {}
    for fact in facts:
        if not fact.value:
            break
        target = TUID(f"figi:{str(fact.value).upper()}")
        if fact.field == "openfigi:shareClassFIGI":
            share_class[fact.subject] = target
        elif fact.field == "openfigi:compositeFIGI":
            composite[fact.subject] = target
    return FigiHierarchy(share_class=share_class, composite=composite)


def x_figi_hierarchy__mutmut_5(facts: Iterable[Fact]) -> FigiHierarchy:
    """Build the level mapping from stored OpenFIGI facts."""
    share_class: dict[TUID, TUID] = {}
    composite: dict[TUID, TUID] = {}
    for fact in facts:
        if not fact.value:
            continue
        target = None
        if fact.field == "openfigi:shareClassFIGI":
            share_class[fact.subject] = target
        elif fact.field == "openfigi:compositeFIGI":
            composite[fact.subject] = target
    return FigiHierarchy(share_class=share_class, composite=composite)


def x_figi_hierarchy__mutmut_6(facts: Iterable[Fact]) -> FigiHierarchy:
    """Build the level mapping from stored OpenFIGI facts."""
    share_class: dict[TUID, TUID] = {}
    composite: dict[TUID, TUID] = {}
    for fact in facts:
        if not fact.value:
            continue
        target = TUID(None)
        if fact.field == "openfigi:shareClassFIGI":
            share_class[fact.subject] = target
        elif fact.field == "openfigi:compositeFIGI":
            composite[fact.subject] = target
    return FigiHierarchy(share_class=share_class, composite=composite)


def x_figi_hierarchy__mutmut_7(facts: Iterable[Fact]) -> FigiHierarchy:
    """Build the level mapping from stored OpenFIGI facts."""
    share_class: dict[TUID, TUID] = {}
    composite: dict[TUID, TUID] = {}
    for fact in facts:
        if not fact.value:
            continue
        target = TUID(f"figi:{str(fact.value).lower()}")
        if fact.field == "openfigi:shareClassFIGI":
            share_class[fact.subject] = target
        elif fact.field == "openfigi:compositeFIGI":
            composite[fact.subject] = target
    return FigiHierarchy(share_class=share_class, composite=composite)


def x_figi_hierarchy__mutmut_8(facts: Iterable[Fact]) -> FigiHierarchy:
    """Build the level mapping from stored OpenFIGI facts."""
    share_class: dict[TUID, TUID] = {}
    composite: dict[TUID, TUID] = {}
    for fact in facts:
        if not fact.value:
            continue
        target = TUID(f"figi:{str(None).upper()}")
        if fact.field == "openfigi:shareClassFIGI":
            share_class[fact.subject] = target
        elif fact.field == "openfigi:compositeFIGI":
            composite[fact.subject] = target
    return FigiHierarchy(share_class=share_class, composite=composite)


def x_figi_hierarchy__mutmut_9(facts: Iterable[Fact]) -> FigiHierarchy:
    """Build the level mapping from stored OpenFIGI facts."""
    share_class: dict[TUID, TUID] = {}
    composite: dict[TUID, TUID] = {}
    for fact in facts:
        if not fact.value:
            continue
        target = TUID(f"figi:{str(fact.value).upper()}")
        if fact.field != "openfigi:shareClassFIGI":
            share_class[fact.subject] = target
        elif fact.field == "openfigi:compositeFIGI":
            composite[fact.subject] = target
    return FigiHierarchy(share_class=share_class, composite=composite)


def x_figi_hierarchy__mutmut_10(facts: Iterable[Fact]) -> FigiHierarchy:
    """Build the level mapping from stored OpenFIGI facts."""
    share_class: dict[TUID, TUID] = {}
    composite: dict[TUID, TUID] = {}
    for fact in facts:
        if not fact.value:
            continue
        target = TUID(f"figi:{str(fact.value).upper()}")
        if fact.field == "XXopenfigi:shareClassFIGIXX":
            share_class[fact.subject] = target
        elif fact.field == "openfigi:compositeFIGI":
            composite[fact.subject] = target
    return FigiHierarchy(share_class=share_class, composite=composite)


def x_figi_hierarchy__mutmut_11(facts: Iterable[Fact]) -> FigiHierarchy:
    """Build the level mapping from stored OpenFIGI facts."""
    share_class: dict[TUID, TUID] = {}
    composite: dict[TUID, TUID] = {}
    for fact in facts:
        if not fact.value:
            continue
        target = TUID(f"figi:{str(fact.value).upper()}")
        if fact.field == "openfigi:shareclassfigi":
            share_class[fact.subject] = target
        elif fact.field == "openfigi:compositeFIGI":
            composite[fact.subject] = target
    return FigiHierarchy(share_class=share_class, composite=composite)


def x_figi_hierarchy__mutmut_12(facts: Iterable[Fact]) -> FigiHierarchy:
    """Build the level mapping from stored OpenFIGI facts."""
    share_class: dict[TUID, TUID] = {}
    composite: dict[TUID, TUID] = {}
    for fact in facts:
        if not fact.value:
            continue
        target = TUID(f"figi:{str(fact.value).upper()}")
        if fact.field == "OPENFIGI:SHARECLASSFIGI":
            share_class[fact.subject] = target
        elif fact.field == "openfigi:compositeFIGI":
            composite[fact.subject] = target
    return FigiHierarchy(share_class=share_class, composite=composite)


def x_figi_hierarchy__mutmut_13(facts: Iterable[Fact]) -> FigiHierarchy:
    """Build the level mapping from stored OpenFIGI facts."""
    share_class: dict[TUID, TUID] = {}
    composite: dict[TUID, TUID] = {}
    for fact in facts:
        if not fact.value:
            continue
        target = TUID(f"figi:{str(fact.value).upper()}")
        if fact.field == "openfigi:shareClassFIGI":
            share_class[fact.subject] = None
        elif fact.field == "openfigi:compositeFIGI":
            composite[fact.subject] = target
    return FigiHierarchy(share_class=share_class, composite=composite)


def x_figi_hierarchy__mutmut_14(facts: Iterable[Fact]) -> FigiHierarchy:
    """Build the level mapping from stored OpenFIGI facts."""
    share_class: dict[TUID, TUID] = {}
    composite: dict[TUID, TUID] = {}
    for fact in facts:
        if not fact.value:
            continue
        target = TUID(f"figi:{str(fact.value).upper()}")
        if fact.field == "openfigi:shareClassFIGI":
            share_class[fact.subject] = target
        elif fact.field != "openfigi:compositeFIGI":
            composite[fact.subject] = target
    return FigiHierarchy(share_class=share_class, composite=composite)


def x_figi_hierarchy__mutmut_15(facts: Iterable[Fact]) -> FigiHierarchy:
    """Build the level mapping from stored OpenFIGI facts."""
    share_class: dict[TUID, TUID] = {}
    composite: dict[TUID, TUID] = {}
    for fact in facts:
        if not fact.value:
            continue
        target = TUID(f"figi:{str(fact.value).upper()}")
        if fact.field == "openfigi:shareClassFIGI":
            share_class[fact.subject] = target
        elif fact.field == "XXopenfigi:compositeFIGIXX":
            composite[fact.subject] = target
    return FigiHierarchy(share_class=share_class, composite=composite)


def x_figi_hierarchy__mutmut_16(facts: Iterable[Fact]) -> FigiHierarchy:
    """Build the level mapping from stored OpenFIGI facts."""
    share_class: dict[TUID, TUID] = {}
    composite: dict[TUID, TUID] = {}
    for fact in facts:
        if not fact.value:
            continue
        target = TUID(f"figi:{str(fact.value).upper()}")
        if fact.field == "openfigi:shareClassFIGI":
            share_class[fact.subject] = target
        elif fact.field == "openfigi:compositefigi":
            composite[fact.subject] = target
    return FigiHierarchy(share_class=share_class, composite=composite)


def x_figi_hierarchy__mutmut_17(facts: Iterable[Fact]) -> FigiHierarchy:
    """Build the level mapping from stored OpenFIGI facts."""
    share_class: dict[TUID, TUID] = {}
    composite: dict[TUID, TUID] = {}
    for fact in facts:
        if not fact.value:
            continue
        target = TUID(f"figi:{str(fact.value).upper()}")
        if fact.field == "openfigi:shareClassFIGI":
            share_class[fact.subject] = target
        elif fact.field == "OPENFIGI:COMPOSITEFIGI":
            composite[fact.subject] = target
    return FigiHierarchy(share_class=share_class, composite=composite)


def x_figi_hierarchy__mutmut_18(facts: Iterable[Fact]) -> FigiHierarchy:
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
            composite[fact.subject] = None
    return FigiHierarchy(share_class=share_class, composite=composite)


def x_figi_hierarchy__mutmut_19(facts: Iterable[Fact]) -> FigiHierarchy:
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
    return FigiHierarchy(share_class=None, composite=composite)


def x_figi_hierarchy__mutmut_20(facts: Iterable[Fact]) -> FigiHierarchy:
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
    return FigiHierarchy(share_class=share_class, composite=None)


def x_figi_hierarchy__mutmut_21(facts: Iterable[Fact]) -> FigiHierarchy:
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
    return FigiHierarchy(composite=composite)


def x_figi_hierarchy__mutmut_22(facts: Iterable[Fact]) -> FigiHierarchy:
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
    return FigiHierarchy(share_class=share_class, )

mutants_x_figi_hierarchy__mutmut['_mutmut_orig'] = x_figi_hierarchy__mutmut_orig # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_1'] = x_figi_hierarchy__mutmut_1 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_2'] = x_figi_hierarchy__mutmut_2 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_3'] = x_figi_hierarchy__mutmut_3 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_4'] = x_figi_hierarchy__mutmut_4 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_5'] = x_figi_hierarchy__mutmut_5 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_6'] = x_figi_hierarchy__mutmut_6 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_7'] = x_figi_hierarchy__mutmut_7 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_8'] = x_figi_hierarchy__mutmut_8 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_9'] = x_figi_hierarchy__mutmut_9 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_10'] = x_figi_hierarchy__mutmut_10 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_11'] = x_figi_hierarchy__mutmut_11 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_12'] = x_figi_hierarchy__mutmut_12 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_13'] = x_figi_hierarchy__mutmut_13 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_14'] = x_figi_hierarchy__mutmut_14 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_15'] = x_figi_hierarchy__mutmut_15 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_16'] = x_figi_hierarchy__mutmut_16 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_17'] = x_figi_hierarchy__mutmut_17 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_18'] = x_figi_hierarchy__mutmut_18 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_19'] = x_figi_hierarchy__mutmut_19 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_20'] = x_figi_hierarchy__mutmut_20 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_21'] = x_figi_hierarchy__mutmut_21 # type: ignore # mutmut generated
mutants_x_figi_hierarchy__mutmut['x_figi_hierarchy__mutmut_22'] = x_figi_hierarchy__mutmut_22 # type: ignore # mutmut generated
mutants_x__identity_candidates__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x__identity_candidates__mutmut)
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


def x__identity_candidates__mutmut_orig(
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


def x__identity_candidates__mutmut_1(
    links: Iterable[IdentifierLink], key: TUID, hierarchy: FigiHierarchy
) -> set[TUID]:
    claimants = None
    # Share-class-linked claimants win outright when any exist.
    linked = [figi for figi in claimants if hierarchy.has_share_class(figi)]
    return {hierarchy.canonical(figi) for figi in (linked or claimants)}


def x__identity_candidates__mutmut_2(
    links: Iterable[IdentifierLink], key: TUID, hierarchy: FigiHierarchy
) -> set[TUID]:
    claimants = [
        link.from_key
        for link in links
        if link.to_key == key or str(link.from_key).startswith("figi:")
    ]
    # Share-class-linked claimants win outright when any exist.
    linked = [figi for figi in claimants if hierarchy.has_share_class(figi)]
    return {hierarchy.canonical(figi) for figi in (linked or claimants)}


def x__identity_candidates__mutmut_3(
    links: Iterable[IdentifierLink], key: TUID, hierarchy: FigiHierarchy
) -> set[TUID]:
    claimants = [
        link.from_key
        for link in links
        if link.to_key != key and str(link.from_key).startswith("figi:")
    ]
    # Share-class-linked claimants win outright when any exist.
    linked = [figi for figi in claimants if hierarchy.has_share_class(figi)]
    return {hierarchy.canonical(figi) for figi in (linked or claimants)}


def x__identity_candidates__mutmut_4(
    links: Iterable[IdentifierLink], key: TUID, hierarchy: FigiHierarchy
) -> set[TUID]:
    claimants = [
        link.from_key
        for link in links
        if link.to_key == key and str(link.from_key).startswith(None)
    ]
    # Share-class-linked claimants win outright when any exist.
    linked = [figi for figi in claimants if hierarchy.has_share_class(figi)]
    return {hierarchy.canonical(figi) for figi in (linked or claimants)}


def x__identity_candidates__mutmut_5(
    links: Iterable[IdentifierLink], key: TUID, hierarchy: FigiHierarchy
) -> set[TUID]:
    claimants = [
        link.from_key
        for link in links
        if link.to_key == key and str(None).startswith("figi:")
    ]
    # Share-class-linked claimants win outright when any exist.
    linked = [figi for figi in claimants if hierarchy.has_share_class(figi)]
    return {hierarchy.canonical(figi) for figi in (linked or claimants)}


def x__identity_candidates__mutmut_6(
    links: Iterable[IdentifierLink], key: TUID, hierarchy: FigiHierarchy
) -> set[TUID]:
    claimants = [
        link.from_key
        for link in links
        if link.to_key == key and str(link.from_key).startswith("XXfigi:XX")
    ]
    # Share-class-linked claimants win outright when any exist.
    linked = [figi for figi in claimants if hierarchy.has_share_class(figi)]
    return {hierarchy.canonical(figi) for figi in (linked or claimants)}


def x__identity_candidates__mutmut_7(
    links: Iterable[IdentifierLink], key: TUID, hierarchy: FigiHierarchy
) -> set[TUID]:
    claimants = [
        link.from_key
        for link in links
        if link.to_key == key and str(link.from_key).startswith("FIGI:")
    ]
    # Share-class-linked claimants win outright when any exist.
    linked = [figi for figi in claimants if hierarchy.has_share_class(figi)]
    return {hierarchy.canonical(figi) for figi in (linked or claimants)}


def x__identity_candidates__mutmut_8(
    links: Iterable[IdentifierLink], key: TUID, hierarchy: FigiHierarchy
) -> set[TUID]:
    claimants = [
        link.from_key
        for link in links
        if link.to_key == key and str(link.from_key).startswith("figi:")
    ]
    # Share-class-linked claimants win outright when any exist.
    linked = None
    return {hierarchy.canonical(figi) for figi in (linked or claimants)}


def x__identity_candidates__mutmut_9(
    links: Iterable[IdentifierLink], key: TUID, hierarchy: FigiHierarchy
) -> set[TUID]:
    claimants = [
        link.from_key
        for link in links
        if link.to_key == key and str(link.from_key).startswith("figi:")
    ]
    # Share-class-linked claimants win outright when any exist.
    linked = [figi for figi in claimants if hierarchy.has_share_class(None)]
    return {hierarchy.canonical(figi) for figi in (linked or claimants)}


def x__identity_candidates__mutmut_10(
    links: Iterable[IdentifierLink], key: TUID, hierarchy: FigiHierarchy
) -> set[TUID]:
    claimants = [
        link.from_key
        for link in links
        if link.to_key == key and str(link.from_key).startswith("figi:")
    ]
    # Share-class-linked claimants win outright when any exist.
    linked = [figi for figi in claimants if hierarchy.has_share_class(figi)]
    return {hierarchy.canonical(None) for figi in (linked or claimants)}


def x__identity_candidates__mutmut_11(
    links: Iterable[IdentifierLink], key: TUID, hierarchy: FigiHierarchy
) -> set[TUID]:
    claimants = [
        link.from_key
        for link in links
        if link.to_key == key and str(link.from_key).startswith("figi:")
    ]
    # Share-class-linked claimants win outright when any exist.
    linked = [figi for figi in claimants if hierarchy.has_share_class(figi)]
    return {hierarchy.canonical(figi) for figi in (linked and claimants)}

mutants_x__identity_candidates__mutmut['_mutmut_orig'] = x__identity_candidates__mutmut_orig # type: ignore # mutmut generated
mutants_x__identity_candidates__mutmut['x__identity_candidates__mutmut_1'] = x__identity_candidates__mutmut_1 # type: ignore # mutmut generated
mutants_x__identity_candidates__mutmut['x__identity_candidates__mutmut_2'] = x__identity_candidates__mutmut_2 # type: ignore # mutmut generated
mutants_x__identity_candidates__mutmut['x__identity_candidates__mutmut_3'] = x__identity_candidates__mutmut_3 # type: ignore # mutmut generated
mutants_x__identity_candidates__mutmut['x__identity_candidates__mutmut_4'] = x__identity_candidates__mutmut_4 # type: ignore # mutmut generated
mutants_x__identity_candidates__mutmut['x__identity_candidates__mutmut_5'] = x__identity_candidates__mutmut_5 # type: ignore # mutmut generated
mutants_x__identity_candidates__mutmut['x__identity_candidates__mutmut_6'] = x__identity_candidates__mutmut_6 # type: ignore # mutmut generated
mutants_x__identity_candidates__mutmut['x__identity_candidates__mutmut_7'] = x__identity_candidates__mutmut_7 # type: ignore # mutmut generated
mutants_x__identity_candidates__mutmut['x__identity_candidates__mutmut_8'] = x__identity_candidates__mutmut_8 # type: ignore # mutmut generated
mutants_x__identity_candidates__mutmut['x__identity_candidates__mutmut_9'] = x__identity_candidates__mutmut_9 # type: ignore # mutmut generated
mutants_x__identity_candidates__mutmut['x__identity_candidates__mutmut_10'] = x__identity_candidates__mutmut_10 # type: ignore # mutmut generated
mutants_x__identity_candidates__mutmut['x__identity_candidates__mutmut_11'] = x__identity_candidates__mutmut_11 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_resolve_instrument__mutmut)
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


def x_resolve_instrument__mutmut_orig(
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


def x_resolve_instrument__mutmut_1(
    links: Iterable[IdentifierLink],
    key: TUID,
    *,
    as_of: datetime,
    hierarchy: FigiHierarchy | None = None,
) -> TUID | None:
    """Resolve any identifier key to its canonical FIGI, as known at
    ``as_of`` (I2). Returns None when no evidence maps the key, or when
    the evidence is genuinely ambiguous — never a guess."""
    hierarchy = None
    visible = [link for link in links if link.knowledge_from <= as_of]
    if str(key).startswith("figi:"):
        return hierarchy.canonical(key)
    candidates = _identity_candidates(visible, key, hierarchy)
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_2(
    links: Iterable[IdentifierLink],
    key: TUID,
    *,
    as_of: datetime,
    hierarchy: FigiHierarchy | None = None,
) -> TUID | None:
    """Resolve any identifier key to its canonical FIGI, as known at
    ``as_of`` (I2). Returns None when no evidence maps the key, or when
    the evidence is genuinely ambiguous — never a guess."""
    hierarchy = hierarchy and FigiHierarchy(share_class={}, composite={})
    visible = [link for link in links if link.knowledge_from <= as_of]
    if str(key).startswith("figi:"):
        return hierarchy.canonical(key)
    candidates = _identity_candidates(visible, key, hierarchy)
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_3(
    links: Iterable[IdentifierLink],
    key: TUID,
    *,
    as_of: datetime,
    hierarchy: FigiHierarchy | None = None,
) -> TUID | None:
    """Resolve any identifier key to its canonical FIGI, as known at
    ``as_of`` (I2). Returns None when no evidence maps the key, or when
    the evidence is genuinely ambiguous — never a guess."""
    hierarchy = hierarchy or FigiHierarchy(share_class=None, composite={})
    visible = [link for link in links if link.knowledge_from <= as_of]
    if str(key).startswith("figi:"):
        return hierarchy.canonical(key)
    candidates = _identity_candidates(visible, key, hierarchy)
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_4(
    links: Iterable[IdentifierLink],
    key: TUID,
    *,
    as_of: datetime,
    hierarchy: FigiHierarchy | None = None,
) -> TUID | None:
    """Resolve any identifier key to its canonical FIGI, as known at
    ``as_of`` (I2). Returns None when no evidence maps the key, or when
    the evidence is genuinely ambiguous — never a guess."""
    hierarchy = hierarchy or FigiHierarchy(share_class={}, composite=None)
    visible = [link for link in links if link.knowledge_from <= as_of]
    if str(key).startswith("figi:"):
        return hierarchy.canonical(key)
    candidates = _identity_candidates(visible, key, hierarchy)
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_5(
    links: Iterable[IdentifierLink],
    key: TUID,
    *,
    as_of: datetime,
    hierarchy: FigiHierarchy | None = None,
) -> TUID | None:
    """Resolve any identifier key to its canonical FIGI, as known at
    ``as_of`` (I2). Returns None when no evidence maps the key, or when
    the evidence is genuinely ambiguous — never a guess."""
    hierarchy = hierarchy or FigiHierarchy(composite={})
    visible = [link for link in links if link.knowledge_from <= as_of]
    if str(key).startswith("figi:"):
        return hierarchy.canonical(key)
    candidates = _identity_candidates(visible, key, hierarchy)
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_6(
    links: Iterable[IdentifierLink],
    key: TUID,
    *,
    as_of: datetime,
    hierarchy: FigiHierarchy | None = None,
) -> TUID | None:
    """Resolve any identifier key to its canonical FIGI, as known at
    ``as_of`` (I2). Returns None when no evidence maps the key, or when
    the evidence is genuinely ambiguous — never a guess."""
    hierarchy = hierarchy or FigiHierarchy(share_class={}, )
    visible = [link for link in links if link.knowledge_from <= as_of]
    if str(key).startswith("figi:"):
        return hierarchy.canonical(key)
    candidates = _identity_candidates(visible, key, hierarchy)
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_7(
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
    visible = None
    if str(key).startswith("figi:"):
        return hierarchy.canonical(key)
    candidates = _identity_candidates(visible, key, hierarchy)
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_8(
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
    visible = [link for link in links if link.knowledge_from < as_of]
    if str(key).startswith("figi:"):
        return hierarchy.canonical(key)
    candidates = _identity_candidates(visible, key, hierarchy)
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_9(
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
    if str(key).startswith(None):
        return hierarchy.canonical(key)
    candidates = _identity_candidates(visible, key, hierarchy)
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_10(
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
    if str(None).startswith("figi:"):
        return hierarchy.canonical(key)
    candidates = _identity_candidates(visible, key, hierarchy)
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_11(
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
    if str(key).startswith("XXfigi:XX"):
        return hierarchy.canonical(key)
    candidates = _identity_candidates(visible, key, hierarchy)
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_12(
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
    if str(key).startswith("FIGI:"):
        return hierarchy.canonical(key)
    candidates = _identity_candidates(visible, key, hierarchy)
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_13(
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
        return hierarchy.canonical(None)
    candidates = _identity_candidates(visible, key, hierarchy)
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_14(
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
    candidates = None
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_15(
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
    candidates = _identity_candidates(None, key, hierarchy)
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_16(
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
    candidates = _identity_candidates(visible, None, hierarchy)
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_17(
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
    candidates = _identity_candidates(visible, key, None)
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_18(
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
    candidates = _identity_candidates(key, hierarchy)
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_19(
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
    candidates = _identity_candidates(visible, hierarchy)
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_20(
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
    candidates = _identity_candidates(visible, key, )
    return candidates.pop() if len(candidates) == 1 else None


def x_resolve_instrument__mutmut_21(
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
    return candidates.pop() if len(candidates) != 1 else None


def x_resolve_instrument__mutmut_22(
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
    return candidates.pop() if len(candidates) == 2 else None

mutants_x_resolve_instrument__mutmut['_mutmut_orig'] = x_resolve_instrument__mutmut_orig # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_1'] = x_resolve_instrument__mutmut_1 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_2'] = x_resolve_instrument__mutmut_2 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_3'] = x_resolve_instrument__mutmut_3 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_4'] = x_resolve_instrument__mutmut_4 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_5'] = x_resolve_instrument__mutmut_5 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_6'] = x_resolve_instrument__mutmut_6 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_7'] = x_resolve_instrument__mutmut_7 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_8'] = x_resolve_instrument__mutmut_8 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_9'] = x_resolve_instrument__mutmut_9 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_10'] = x_resolve_instrument__mutmut_10 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_11'] = x_resolve_instrument__mutmut_11 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_12'] = x_resolve_instrument__mutmut_12 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_13'] = x_resolve_instrument__mutmut_13 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_14'] = x_resolve_instrument__mutmut_14 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_15'] = x_resolve_instrument__mutmut_15 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_16'] = x_resolve_instrument__mutmut_16 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_17'] = x_resolve_instrument__mutmut_17 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_18'] = x_resolve_instrument__mutmut_18 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_19'] = x_resolve_instrument__mutmut_19 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_20'] = x_resolve_instrument__mutmut_20 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_21'] = x_resolve_instrument__mutmut_21 # type: ignore # mutmut generated
mutants_x_resolve_instrument__mutmut['x_resolve_instrument__mutmut_22'] = x_resolve_instrument__mutmut_22 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_conflicts__mutmut)
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


def x_conflicts__mutmut_orig(
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


def x_conflicts__mutmut_1(
    links: Iterable[IdentifierLink], hierarchy: FigiHierarchy | None = None
) -> list[tuple[TUID, list[TUID]]]:
    """Identifier keys claimed by more than one canonical FIGI.

    Many venue-level FIGIs per identifier is the hierarchy working as
    designed. Two distinct *canonical* instruments claiming one ISIN is a
    real data problem, surfaced for review rather than silently resolved
    (spec §8.1.4: report disagreement, never pick a winner).
    """
    hierarchy = None
    link_list = list(links)
    keys = {link.to_key for link in link_list if str(link.from_key).startswith("figi:")}
    found: list[tuple[TUID, list[TUID]]] = []
    for key in keys:
        candidates = _identity_candidates(link_list, key, hierarchy)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_2(
    links: Iterable[IdentifierLink], hierarchy: FigiHierarchy | None = None
) -> list[tuple[TUID, list[TUID]]]:
    """Identifier keys claimed by more than one canonical FIGI.

    Many venue-level FIGIs per identifier is the hierarchy working as
    designed. Two distinct *canonical* instruments claiming one ISIN is a
    real data problem, surfaced for review rather than silently resolved
    (spec §8.1.4: report disagreement, never pick a winner).
    """
    hierarchy = hierarchy and FigiHierarchy(share_class={}, composite={})
    link_list = list(links)
    keys = {link.to_key for link in link_list if str(link.from_key).startswith("figi:")}
    found: list[tuple[TUID, list[TUID]]] = []
    for key in keys:
        candidates = _identity_candidates(link_list, key, hierarchy)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_3(
    links: Iterable[IdentifierLink], hierarchy: FigiHierarchy | None = None
) -> list[tuple[TUID, list[TUID]]]:
    """Identifier keys claimed by more than one canonical FIGI.

    Many venue-level FIGIs per identifier is the hierarchy working as
    designed. Two distinct *canonical* instruments claiming one ISIN is a
    real data problem, surfaced for review rather than silently resolved
    (spec §8.1.4: report disagreement, never pick a winner).
    """
    hierarchy = hierarchy or FigiHierarchy(share_class=None, composite={})
    link_list = list(links)
    keys = {link.to_key for link in link_list if str(link.from_key).startswith("figi:")}
    found: list[tuple[TUID, list[TUID]]] = []
    for key in keys:
        candidates = _identity_candidates(link_list, key, hierarchy)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_4(
    links: Iterable[IdentifierLink], hierarchy: FigiHierarchy | None = None
) -> list[tuple[TUID, list[TUID]]]:
    """Identifier keys claimed by more than one canonical FIGI.

    Many venue-level FIGIs per identifier is the hierarchy working as
    designed. Two distinct *canonical* instruments claiming one ISIN is a
    real data problem, surfaced for review rather than silently resolved
    (spec §8.1.4: report disagreement, never pick a winner).
    """
    hierarchy = hierarchy or FigiHierarchy(share_class={}, composite=None)
    link_list = list(links)
    keys = {link.to_key for link in link_list if str(link.from_key).startswith("figi:")}
    found: list[tuple[TUID, list[TUID]]] = []
    for key in keys:
        candidates = _identity_candidates(link_list, key, hierarchy)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_5(
    links: Iterable[IdentifierLink], hierarchy: FigiHierarchy | None = None
) -> list[tuple[TUID, list[TUID]]]:
    """Identifier keys claimed by more than one canonical FIGI.

    Many venue-level FIGIs per identifier is the hierarchy working as
    designed. Two distinct *canonical* instruments claiming one ISIN is a
    real data problem, surfaced for review rather than silently resolved
    (spec §8.1.4: report disagreement, never pick a winner).
    """
    hierarchy = hierarchy or FigiHierarchy(composite={})
    link_list = list(links)
    keys = {link.to_key for link in link_list if str(link.from_key).startswith("figi:")}
    found: list[tuple[TUID, list[TUID]]] = []
    for key in keys:
        candidates = _identity_candidates(link_list, key, hierarchy)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_6(
    links: Iterable[IdentifierLink], hierarchy: FigiHierarchy | None = None
) -> list[tuple[TUID, list[TUID]]]:
    """Identifier keys claimed by more than one canonical FIGI.

    Many venue-level FIGIs per identifier is the hierarchy working as
    designed. Two distinct *canonical* instruments claiming one ISIN is a
    real data problem, surfaced for review rather than silently resolved
    (spec §8.1.4: report disagreement, never pick a winner).
    """
    hierarchy = hierarchy or FigiHierarchy(share_class={}, )
    link_list = list(links)
    keys = {link.to_key for link in link_list if str(link.from_key).startswith("figi:")}
    found: list[tuple[TUID, list[TUID]]] = []
    for key in keys:
        candidates = _identity_candidates(link_list, key, hierarchy)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_7(
    links: Iterable[IdentifierLink], hierarchy: FigiHierarchy | None = None
) -> list[tuple[TUID, list[TUID]]]:
    """Identifier keys claimed by more than one canonical FIGI.

    Many venue-level FIGIs per identifier is the hierarchy working as
    designed. Two distinct *canonical* instruments claiming one ISIN is a
    real data problem, surfaced for review rather than silently resolved
    (spec §8.1.4: report disagreement, never pick a winner).
    """
    hierarchy = hierarchy or FigiHierarchy(share_class={}, composite={})
    link_list = None
    keys = {link.to_key for link in link_list if str(link.from_key).startswith("figi:")}
    found: list[tuple[TUID, list[TUID]]] = []
    for key in keys:
        candidates = _identity_candidates(link_list, key, hierarchy)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_8(
    links: Iterable[IdentifierLink], hierarchy: FigiHierarchy | None = None
) -> list[tuple[TUID, list[TUID]]]:
    """Identifier keys claimed by more than one canonical FIGI.

    Many venue-level FIGIs per identifier is the hierarchy working as
    designed. Two distinct *canonical* instruments claiming one ISIN is a
    real data problem, surfaced for review rather than silently resolved
    (spec §8.1.4: report disagreement, never pick a winner).
    """
    hierarchy = hierarchy or FigiHierarchy(share_class={}, composite={})
    link_list = list(None)
    keys = {link.to_key for link in link_list if str(link.from_key).startswith("figi:")}
    found: list[tuple[TUID, list[TUID]]] = []
    for key in keys:
        candidates = _identity_candidates(link_list, key, hierarchy)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_9(
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
    keys = None
    found: list[tuple[TUID, list[TUID]]] = []
    for key in keys:
        candidates = _identity_candidates(link_list, key, hierarchy)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_10(
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
    keys = {link.to_key for link in link_list if str(link.from_key).startswith(None)}
    found: list[tuple[TUID, list[TUID]]] = []
    for key in keys:
        candidates = _identity_candidates(link_list, key, hierarchy)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_11(
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
    keys = {link.to_key for link in link_list if str(None).startswith("figi:")}
    found: list[tuple[TUID, list[TUID]]] = []
    for key in keys:
        candidates = _identity_candidates(link_list, key, hierarchy)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_12(
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
    keys = {link.to_key for link in link_list if str(link.from_key).startswith("XXfigi:XX")}
    found: list[tuple[TUID, list[TUID]]] = []
    for key in keys:
        candidates = _identity_candidates(link_list, key, hierarchy)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_13(
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
    keys = {link.to_key for link in link_list if str(link.from_key).startswith("FIGI:")}
    found: list[tuple[TUID, list[TUID]]] = []
    for key in keys:
        candidates = _identity_candidates(link_list, key, hierarchy)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_14(
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
    found: list[tuple[TUID, list[TUID]]] = None
    for key in keys:
        candidates = _identity_candidates(link_list, key, hierarchy)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_15(
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
        candidates = None
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_16(
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
        candidates = _identity_candidates(None, key, hierarchy)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_17(
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
        candidates = _identity_candidates(link_list, None, hierarchy)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_18(
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
        candidates = _identity_candidates(link_list, key, None)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_19(
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
        candidates = _identity_candidates(key, hierarchy)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_20(
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
        candidates = _identity_candidates(link_list, hierarchy)
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_21(
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
        candidates = _identity_candidates(link_list, key, )
        if len(candidates) > 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_22(
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
        if len(candidates) >= 1:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_23(
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
        if len(candidates) > 2:
            found.append((key, sorted(candidates)))
    return found


def x_conflicts__mutmut_24(
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
            found.append(None)
    return found


def x_conflicts__mutmut_25(
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
            found.append((key, sorted(None)))
    return found

mutants_x_conflicts__mutmut['_mutmut_orig'] = x_conflicts__mutmut_orig # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_1'] = x_conflicts__mutmut_1 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_2'] = x_conflicts__mutmut_2 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_3'] = x_conflicts__mutmut_3 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_4'] = x_conflicts__mutmut_4 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_5'] = x_conflicts__mutmut_5 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_6'] = x_conflicts__mutmut_6 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_7'] = x_conflicts__mutmut_7 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_8'] = x_conflicts__mutmut_8 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_9'] = x_conflicts__mutmut_9 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_10'] = x_conflicts__mutmut_10 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_11'] = x_conflicts__mutmut_11 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_12'] = x_conflicts__mutmut_12 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_13'] = x_conflicts__mutmut_13 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_14'] = x_conflicts__mutmut_14 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_15'] = x_conflicts__mutmut_15 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_16'] = x_conflicts__mutmut_16 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_17'] = x_conflicts__mutmut_17 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_18'] = x_conflicts__mutmut_18 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_19'] = x_conflicts__mutmut_19 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_20'] = x_conflicts__mutmut_20 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_21'] = x_conflicts__mutmut_21 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_22'] = x_conflicts__mutmut_22 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_23'] = x_conflicts__mutmut_23 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_24'] = x_conflicts__mutmut_24 # type: ignore # mutmut generated
mutants_x_conflicts__mutmut['x_conflicts__mutmut_25'] = x_conflicts__mutmut_25 # type: ignore # mutmut generated
