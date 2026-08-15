"""`RELS` — related securities (spec §9.5).

What else a reader can look at, given the bond in front of them. Two kinds
of relation, and they are not the same claim:

* **Same issuer** — other bonds from the identical legal entity. A curve is
  fitted across exactly this set, so it is the set `TVAL` already treats as
  one credit.
* **Corporate family** — bonds from entities sharing an ultimate parent per
  GLEIF. Bayer US Finance and Bayer US Finance II are separate LEIs, file
  separately, and are one credit to anyone trading them.

**"Related" here means shared legal ownership, not similarity.** Two
utilities in the same state with the same rating are not related by this
screen, and a bank's captive leasing arm is. That is a narrower and more
defensible relation than sector — it comes from a registry rather than from
a classification somebody chose — and it is the only one this store can
support, since sector and rating are both absent.

**The family size and the reachable count are both published.** On the live
store a Bayer financing entity sits under a parent with **130 entities**, of
which **one** has a bond in any N-PORT filing here. Showing only the one
would present a vast corporate group as a pair. Showing only the 129 would
imply 129 tradeable lines. The gap between them is the coverage of this
install, and it belongs on the screen rather than in a footnote.

Coverage is thin and measured: of 154 bond issuers, 52 appear in the GLEIF
relationship graph at all, and 33 of those have siblings.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from treble.core.entity_graph import DIRECT_PARENT_TYPE, ULTIMATE_PARENT_TYPE
from treble.core.identifiers import TUID
from treble.store.duck import DuckStore
from treble.tapi.entity import (
    Ancestry,
    EntityUnknownError,
    ancestry_of,
    children_of,
)

#: Only straight debt, for the reason `DDIS` gives: a securitisation vehicle
#: is a corporation and filers classify it CORP, so the asset category is
#: what separates a bond from a CLO tranche.
CORPORATE_DEBT = frozenset({"DBT"})


class NoRelationsError(ValueError):
    """Nothing related could be found, with the reason distinguished.

    An instrument with no issuer LEI, an issuer absent from the GLEIF
    graph, and an issuer whose family holds nothing else are three
    different findings. Collapsed into an empty table they read as "this
    bond is unrelated to anything", which is never true and merely
    unmeasurable here.
    """


@dataclass(frozen=True)
class RelatedSecurity:
    """One security related to the subject, and how."""

    relationship: str
    identifier: str
    issuer: str | None
    maturity: date | None
    coupon_pct: float | None
    lei: str


@dataclass(frozen=True)
class RelatedSet:
    """Everything `RELS` shows for one bond."""

    subject: str
    lei: str
    issuer: str | None
    ultimate_parent: str | None
    #: Every entity GLEIF puts under the same parent, this one included,
    #: whether or not this install holds any of their paper. Counted from
    #: the registry rather than from the rows below, which is the whole
    #: point: the two numbers differ by the coverage of these filings.
    family_size: int
    same_issuer: tuple[RelatedSecurity, ...]
    family: tuple[RelatedSecurity, ...]

    @property
    def reachable(self) -> int:
        return len(self.same_issuer) + len(self.family)


def _bond_index(
    store: DuckStore, *, as_of: datetime
) -> dict[str, list[tuple[str, dict[str, object]]]]:
    """Debt holdings grouped by issuer LEI, newest report per bond."""
    from treble.tapi.issuer_curves import bond_rows

    latest: dict[str, dict[str, object]] = {}
    for row in bond_rows(store, as_of=as_of):
        if row.get("nport:assetCat") not in CORPORATE_DEBT:
            continue
        identifier = str(row.get("identifier"))
        seen = latest.get(identifier)
        current = row.get("report_date")
        if seen is None or (
            isinstance(current, date)
            and isinstance(seen.get("report_date"), date)
            and current > seen["report_date"]  # type: ignore[operator]
        ):
            latest[identifier] = row

    index: dict[str, list[tuple[str, dict[str, object]]]] = defaultdict(list)
    for identifier, row in latest.items():
        # Registry over filer, as everywhere else: GLEIF's LEI is the
        # issuer's own registration and N-PORT's is what a fund believed.
        lei = str(row.get("gleif:lei") or row.get("nport:lei") or "").upper()
        if lei:
            index[lei].append((identifier, row))
    return index


def _security(
    relationship: str, identifier: str, row: dict[str, object], lei: str
) -> RelatedSecurity:
    maturity = row.get("nport:maturityDt")
    coupon = row.get("nport:annualizedRt")
    name = row.get("nport:name")
    return RelatedSecurity(
        relationship=relationship,
        identifier=identifier,
        issuer=str(name) if isinstance(name, str) else None,
        maturity=maturity if isinstance(maturity, date) else None,
        # Percent, as N-PORT reports it. Named with its unit for the reason
        # `DDIS` names it: decimal and percent differ by a hundred and both
        # look plausible in a column.
        coupon_pct=float(coupon) if isinstance(coupon, float | int) else None,
        lei=lei,
    )


def related_securities(store: DuckStore, *, identifier: str, as_of: datetime) -> RelatedSet:
    """Same-issuer and corporate-family bonds for one holding."""
    index = _bond_index(store, as_of=as_of)
    mine = next(
        (
            (lei, row)
            for lei, entries in index.items()
            for ident, row in entries
            if ident == identifier
        ),
        None,
    )
    if mine is None:
        raise NoRelationsError(
            f"{identifier} is not a straight-debt holding in any filing held here, so it "
            "has no issuer to relate it by"
        )
    lei, row = mine
    name = row.get("nport:name")

    same = tuple(
        _security("same issuer", ident, other, lei)
        for ident, other in sorted(index[lei])
        if ident != identifier
    )

    ultimate: str | None = None
    family_size = 0
    family: list[RelatedSecurity] = []
    ancestry: Ancestry | None
    try:
        ancestry = ancestry_of(store, TUID(f"lei:{lei}"), as_of=as_of)
    except EntityUnknownError:
        ancestry = None
    if ancestry is not None:
        # The relationship used to find the parent must be the one used to
        # find its children. GLEIF states direct and ultimate parents
        # separately and they disagree often — three of six entities
        # sampled from this store — so taking the ultimate parent and then
        # asking for its *direct* children returns a different family, and
        # one that looks entirely reasonable.
        parent: TUID | None
        if ancestry.ultimate_parent is not None:
            parent, relationship = ancestry.ultimate_parent, ULTIMATE_PARENT_TYPE
        else:
            parent, relationship = ancestry.direct_parent, DIRECT_PARENT_TYPE
        if parent is not None:
            ultimate = str(parent)
            siblings = children_of(store, parent, as_of=as_of, relationship_type=relationship)
            family_size = len(siblings)
            for sibling in sorted(siblings, key=str):
                sibling_lei = str(sibling).split(":", 1)[-1].upper()
                if sibling_lei == lei:
                    continue
                for ident, other in sorted(index.get(sibling_lei, [])):
                    family.append(_security("same parent", ident, other, sibling_lei))

    if not same and not family:
        # Three distinguishable findings, and the message says which.
        if ancestry is None:
            reason = "its issuer has no GLEIF relationship record"
        elif ultimate is None:
            reason = "GLEIF records no parent for its issuer"
        else:
            reason = f"none of the {family_size} entities under {ultimate} has other paper here"
        raise NoRelationsError(
            f"{identifier} has no related security in this store: {reason}. That is the "
            "coverage of these filings, not a statement that the bond stands alone"
        )

    return RelatedSet(
        subject=identifier,
        lei=lei,
        issuer=str(name) if isinstance(name, str) else None,
        ultimate_parent=ultimate,
        family_size=family_size,
        same_issuer=same,
        family=tuple(family),
    )


__all__ = [
    "CORPORATE_DEBT",
    "NoRelationsError",
    "RelatedSecurity",
    "RelatedSet",
    "related_securities",
]
