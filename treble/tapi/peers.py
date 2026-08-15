"""Relative value against comparable bonds, for issuers with no curve.

`TVAL`'s prong 2 fits a yield curve across an issuer's own outstanding debt.
That is the better method — it holds credit constant and varies only
maturity — and it needs three bonds from one issuer. On this store **28 of
153 issuers clear that bar, so 157 of 269 bonds got no relative-value call
at all**: not a refusal on screen, simply absent from the ranking.

`ComparableSet` in `analytics/tval/relative.py` is the machinery for exactly
those bonds, and it was built, tested and called by nothing outside its own
suite. It selects peers on the dimensions this store *has* — currency,
issuer category, maturity proximity — and names the ones it could not use.

**A peer call is a weaker claim than a curve call, and the screen must not
let them blur.** An issuer curve compares a bond with its own issuer's other
paper: same credit, same seniority, same everything except maturity. A peer
set compares it with *other companies'* bonds matched on three dimensions
that do not include rating, sector or seniority — so a bond can be "cheap"
against its peers purely by being a worse credit, which is not a finding,
it is the definition of a spread.

So two things are published with every call:

* **the dispersion of the peer set.** A bond 80bp above a peer group whose
  own yields span 400bp is inside the noise, exactly as a rich/cheap call
  against a curve with 100bp of residual scatter is. The issuer-curve tab
  already labels those "(in noise)" and this uses the same idea.
* **the dimensions not matched on.** `ComparableSet` carries them, and they
  are the reason the call is weak rather than a footnote about data.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime

from treble.analytics.tval.relative import (
    DEFAULT_MATURITY_WINDOW,
    ComparableSet,
    IssuerBond,
)
from treble.store.duck import DuckStore

#: A peer group smaller than this is one or two bonds, and a median of two
#: is a midpoint between two companies rather than a market level.
MIN_PEERS = 4

#: A call inside this multiple of the peer group's own spread is reported as
#: indistinguishable from it. One standard deviation: wider would hide real
#: calls, narrower would present scatter as signal.
NOISE_MULTIPLE = 1.0


class NoPeersError(ValueError):
    """No comparable set could be assembled for this bond."""


@dataclass(frozen=True)
class PeerValue:
    """One bond against its peers, and how much to trust it."""

    identifier: str
    issuer: str | None
    maturity: date
    yield_pct: float
    peer_count: int
    #: How many bonds the comparable set chose from. Published beside the
    #: peer count because the ratio is the honest measure of how selective
    #: the match was: on this store a "peer group" is routinely 226 of 233
    #: bonds, which is a market level wearing the word peer. With rating,
    #: sector and seniority all absent, that is what the dimensions this
    #: store has can deliver, and a reader should see it rather than infer
    #: it.
    universe_size: int
    peer_median_pct: float
    #: Standard deviation of the peer yields. The number that decides
    #: whether the call below means anything.
    peer_dispersion_pct: float
    dimensions: tuple[str, ...]
    missing_dimensions: tuple[str, ...]
    report_date: date

    @property
    def residual_bp(self) -> float:
        """Positive is cheap: yielding more than the peer median."""
        return (self.yield_pct - self.peer_median_pct) * 100.0

    @property
    def in_noise(self) -> bool:
        """Whether the call is smaller than the peer group's own spread."""
        return abs(self.residual_bp) <= NOISE_MULTIPLE * self.peer_dispersion_pct * 100.0

    @property
    def verdict(self) -> str:
        if self.in_noise:
            return "in noise"
        return "cheap" if self.residual_bp > 0 else "rich"


def peer_values(
    store: DuckStore,
    *,
    as_of: datetime,
    only_unfitted: bool = True,
    maturity_window: float = DEFAULT_MATURITY_WINDOW,
) -> tuple[PeerValue, ...]:
    """Peer-relative value for bonds, cheapest first.

    `only_unfitted` restricts the result to bonds whose issuer has no
    curve, which is the set this exists for. Passing False values the whole
    universe — useful for checking that a peer call and a curve call agree
    in sign on the bonds where both exist, which is the only external check
    available on a method with no traded prices behind it.
    """
    from treble.tapi.issuer_curves import IssuerCurvesUnavailableError, build_issuer_curves

    try:
        curves = build_issuer_curves(store, as_of=as_of)
    except IssuerCurvesUnavailableError as error:
        raise NoPeersError(f"no bond universe to compare against: {error}") from error

    universe: list[IssuerBond] = [bond for bonds in curves.universe.values() for bond in bonds]
    if len(universe) <= MIN_PEERS:
        raise NoPeersError(
            f"{len(universe)} bond(s) on {curves.report_date} is not a peer group; "
            f"{MIN_PEERS} is the fewest a median means anything over"
        )

    lei_of = {bond.identifier: lei for lei, bonds in curves.universe.items() for bond in bonds}
    out: list[PeerValue] = []
    for bond in universe:
        lei = lei_of[bond.identifier]
        if only_unfitted and lei in curves.curves:
            continue
        comparable = ComparableSet.around(
            bond, universe, as_of=curves.report_date, maturity_window=maturity_window
        )
        yields = [b.yield_ for b in universe if b.identifier in set(comparable.members)]
        if len(yields) < MIN_PEERS:
            # Skipped rather than reported with a two-bond median. A weak
            # call is worse than no call: it enters the ranking and is read
            # like the rest.
            continue
        out.append(
            PeerValue(
                identifier=bond.identifier,
                issuer=curves.names.get(lei),
                maturity=bond.maturity,
                yield_pct=bond.yield_ * 100.0,
                peer_count=len(yields),
                universe_size=len(universe),
                peer_median_pct=statistics.median(yields) * 100.0,
                peer_dispersion_pct=statistics.pstdev(yields) * 100.0,
                dimensions=comparable.dimensions,
                missing_dimensions=comparable.missing_dimensions,
                report_date=curves.report_date,
            )
        )
    if not out:
        raise NoPeersError(
            f"no bond on {curves.report_date} had {MIN_PEERS} comparables on currency, "
            "issuer category and maturity proximity"
        )
    # Cheapest first, but the significant ones ahead of the noise: a screen
    # ordered by residual alone puts the widest peer groups at the top,
    # which is the same defect the issuer-curve ranking had to fix.
    return tuple(sorted(out, key=lambda v: (v.in_noise, -v.residual_bp)))


__all__ = [
    "MIN_PEERS",
    "NOISE_MULTIPLE",
    "NoPeersError",
    "PeerValue",
    "peer_values",
]
