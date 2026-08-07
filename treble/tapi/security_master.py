"""The security master, reachable at last (spec §9.3-9.5).

`core/master.py` has built links, a FIGI hierarchy and a point-in-time
resolver since WP7. Nothing called it. `tapi/local.py` resolved through a
ticker index and its own comment said so — *"descriptor-based resolution
needs a security-master ... unbuilt lookup"* — while the lookup sat one
import away, tested and inert. The reachability gate is what finally said
it out loud.

This is the bridge: stored facts in, canonical FIGI out.

**Rebuilt from facts, never accumulated.** `links_from_facts` is a pure
function of the store, so the master is reproducible by replay (I5) rather
than being state that drifts from the evidence. That costs a pass over the
mapping facts on each build, which is why :class:`SecurityMaster` is built
once and queried many times rather than rebuilt per lookup.

**Point-in-time, because resolution is a fact like any other (I2).** Which
FIGI a CUSIP mapped to last March is a different question from which it
maps to today, and a resolver that answered the second when asked the first
would make every historical screen quietly wrong — the bond would be
right, the identity behind it would be today's.

**Ambiguity resolves to nothing, not to a guess.** `resolve_instrument`
returns `None` where the evidence disagrees, and this passes that through.
A master that picked the most-cited candidate would be inventing an
identity no source asserted, which is the one thing §9 forbids outright.
:meth:`SecurityMaster.conflicts_for` is how a caller asks *why* nothing
came back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from treble.core.identifiers import TUID
from treble.core.master import (
    MAPPING_FIELDS,
    FigiHierarchy,
    IdentifierLink,
    conflicting_links,
    figi_hierarchy,
    links_from_facts,
    resolve_instrument,
)
from treble.store.duck import DuckStore

#: Fields the hierarchy is built from, alongside the mapping fields.
_HIERARCHY_FIELDS = ("openfigi:shareClassFIGI", "openfigi:compositeFIGI")


@dataclass(frozen=True)
class SecurityMaster:
    """Resolution over the evidence in the store, as of one knowledge time."""

    links: tuple[IdentifierLink, ...]
    hierarchy: FigiHierarchy
    as_of: datetime

    def resolve(self, key: TUID) -> TUID | None:
        """The canonical FIGI for any identifier key, or `None`.

        `None` covers two different situations and the caller usually wants
        to know which: no evidence at all, and evidence that disagrees.
        `conflicts_for` separates them.
        """
        return resolve_instrument(self.links, key, as_of=self.as_of, hierarchy=self.hierarchy)

    def conflicts_for(self, key: TUID) -> dict[str, tuple[TUID, ...]]:
        """Which kinds this key maps to more than one of.

        Empty when resolution failed for want of evidence rather than for
        want of agreement. A screen showing "unresolved" for both would
        hide that two sources are contradicting each other, which is a data
        problem somebody can act on.
        """
        found = conflicting_links(self.links)
        return {
            kind: tuple(sorted({link.to_key for link in group}))
            for (subject, kind), group in found.items()
            if subject == key
        }

    @property
    def instrument_count(self) -> int:
        """Distinct subjects the master has any evidence about."""
        return len({link.from_key for link in self.links})


def build_security_master(store: DuckStore, *, as_of: datetime) -> SecurityMaster:
    """Build the master from every mapping fact visible at `as_of`.

    Reads through the store's own point-in-time API rather than a query of
    its own, for the reason every other service here does: a master built
    on a different visibility rule from the rest of the system would answer
    "what was this on Tuesday" differently from the screen that asked.
    """
    mapping_facts = []
    hierarchy_facts = []
    wanted = set(MAPPING_FIELDS)
    for prefix in ("isin:", "cusip:", "figi:", "ticker:"):
        for subject in store.subjects_with_prefix(prefix, as_of=as_of):
            for fact in store.subject_facts(TUID(str(subject)), as_of=as_of):
                if fact.field in wanted:
                    mapping_facts.append(fact)
                elif fact.field in _HIERARCHY_FIELDS:
                    hierarchy_facts.append(fact)

    return SecurityMaster(
        links=tuple(links_from_facts(mapping_facts)),
        hierarchy=figi_hierarchy(hierarchy_facts),
        as_of=as_of,
    )


__all__ = ["SecurityMaster", "build_security_master"]
