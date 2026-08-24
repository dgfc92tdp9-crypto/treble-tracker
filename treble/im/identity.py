"""What a Matrix ID actually proves about who someone works for.

    Directory-backed verified identity — every participant is a verified
    professional at a named institution, with employer verification via
    domain control and LEI cross-reference. No anonymous accounts.
    — spec §19.1

That sentence describes a chain, and each link proves something
different. Keeping them apart is the whole module, because the strength
of the claim `PEOP` prints is the strength of the *weakest* link, and
collapsing them into one boolean would publish the strongest.

**Link 1 — the localpart proves nothing.** `@jane:acme.com` says a
person answers to `jane` on that homeserver. It is a username.

**Link 2 — the domain proves control of a homeserver, and that is
real.** You cannot obtain `@anyone:acme.com` unless acme.com's
homeserver issues it, and running that homeserver requires control of
the domain's DNS and TLS. So a Matrix ID is domain-control evidence in a
way an email address is not: an email `From` header is asserted by the
sender, whereas an MXID is asserted by the server that authenticated it.
That link is only as strong as the *authentication*, which is why
:func:`verify` demands a `whoami` round trip rather than parsing a
string somebody typed.

**Link 3 — domain to legal entity is a cross-reference, not a proof.**
GLEIF publishes no domain field. Matching `acme.com` to an LEI means
matching a *name*, and names do not normalise: "Acme Corp",
"Acme Corporation" and "Acme Corp." are one company, while "Acme
Holdings" may be another. So a match here is reported as a candidate
with the name that matched, never as a settled fact, and a
non-match is reported as *not found* rather than as a failure — the
entity may simply not be in the store.

The result is that `PEOP` can say "domain verified, entity unmatched",
which is both true and useful, instead of "verified", which would be one
word covering three different amounts of evidence.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from datetime import datetime

from treble.store.duck import DuckStore

#: `@localpart:domain`. The grammar is from the Matrix spec: a localpart
#: of the permitted characters, then a colon, then a server name which
#: may carry a port. Anchored, because a partial match would accept
#: `not-an-id @jane:acme.com trailing` and treat it as an identity.
MXID = re.compile(r"\A@(?P<localpart>[a-z0-9._=/+-]+):(?P<domain>[A-Za-z0-9.\-\[\]:]+)\Z")

#: The GLEIF field carrying an entity's registered legal name.
LEGAL_NAME_FIELD = "gleif:legalName"


class Strength(enum.Enum):
    """How much of the identity chain has actually been established."""

    #: Nothing checked. A string that looks like an MXID.
    ASSERTED = "asserted"
    #: A homeserver authenticated this MXID: domain control is proven.
    DOMAIN_VERIFIED = "domain-verified"
    #: Domain control proven, *and* the domain's label matched the first
    #: word of a registered legal name. **A candidate, not a match**, and
    #: named so on purpose: `old.com` matches "Old Dominion Freight Line,
    #: Inc." and has nothing to do with it. The matched name is always
    #: published beside this so a reader judges it rather than trusting
    #: the word — calling it ENTITY_MATCHED, as this first did, would put
    #: a confident label on a coincidence.
    ENTITY_CANDIDATE = "entity-candidate"


class IdentityError(ValueError):
    """The identity could not be read as a Matrix ID."""


@dataclass(frozen=True)
class MatrixIdentity:
    """One Matrix ID and everything established about it."""

    mxid: str
    localpart: str
    domain: str
    strength: Strength
    #: The GLEIF LEI a name match suggested, if any. Never inferred from
    #: the domain alone.
    lei: str | None = None
    #: The registered name that matched, so a reader can judge the match
    #: rather than trust it.
    matched_name: str | None = None
    verified_at: datetime | None = None

    @property
    def employer_domain(self) -> str:
        """The domain, with any port stripped — a homeserver may run on
        one, and `acme.com:8448` is the same employer as `acme.com`."""
        host = self.domain
        if host.startswith("["):  # bracketed IPv6 literal
            return host
        return host.split(":", 1)[0]

    @property
    def checked(self) -> str:
        """When the homeserver round trip happened, or that it did not.

        On screen beside the strength, because domain verification is a
        statement about a moment: a token revoked since is still recorded
        here as having verified then, and a reader needs the date to
        judge whether that is still worth anything.
        """
        if self.verified_at is None:
            return "never checked"
        return self.verified_at.date().isoformat()

    def describe(self) -> str:
        """One line for a screen, saying exactly how much is known."""
        match self.strength:
            case Strength.ASSERTED:
                return "asserted — nothing checked"
            case Strength.DOMAIN_VERIFIED:
                return f"domain {self.employer_domain} verified; no GLEIF entity matched"
            case Strength.ENTITY_CANDIDATE:
                return (
                    f"domain {self.employer_domain} verified; "
                    f"possible entity {self.matched_name} — name match, unconfirmed"
                )


def parse(mxid: str) -> tuple[str, str]:
    """Split a Matrix ID, refusing anything that is not one.

    Refuses rather than returning a partial: an identity that fails to
    parse is not an identity with an empty domain, and treating it as one
    would put a blank employer beside a real name.
    """
    match = MXID.match(mxid.strip())
    if match is None:
        raise IdentityError(f"{mxid!r} is not a Matrix ID: expected @localpart:domain")
    return match.group("localpart"), match.group("domain")


def cross_reference(
    store: DuckStore, domain: str, *, as_of: datetime
) -> tuple[str | None, str | None]:
    """Find a GLEIF entity whose registered name matches a domain.

    Returns (lei, matched_name), or (None, None). **A candidate, not a
    proof** — GLEIF publishes no domain field, so this matches the
    domain's second-level label against registered legal names, and
    names do not normalise. `acme.com` matching "ACME CORP" is
    suggestive; it is not evidence that the homeserver belongs to that
    legal entity.

    Matched on the **first word** of the legal name, not the whole
    string. The first version compared the domain label against the
    entire name — `KENVUE` against `KENVUE INC.` — which could never
    match anything, a lookup incapable of firing. Found by running it
    against the store rather than by reading it.

    First-word matching can fire, and can also be wrong: `old.com`
    matches "Old Dominion Freight Line, Inc." and has nothing to do with
    it. That is why the caller receives the name and reports a
    *candidate*. A stricter rule that never matched would be no safer,
    only quieter.

    An ambiguous match is no match: two entities sharing a first word
    return nothing rather than the alphabetically first, which has
    nothing to do with which is right.
    """
    label = domain.split(":", 1)[0].split(".")[0].strip().upper()
    if not label:
        return None, None
    rows = store._conn.execute(
        """
        SELECT subject, value_text FROM all_facts
        WHERE field = ?
          AND upper(split_part(replace(value_text, ',', ' '), ' ', 1)) = ?
          AND knowledge_from <= ?
        ORDER BY subject LIMIT 2
        """,
        [LEGAL_NAME_FIELD, label, as_of],
    ).fetchall()
    if len(rows) != 1:
        # Zero is no match. **Two or more is also no match**: an ambiguous
        # cross-reference must not silently pick the first, because the
        # first is sorted alphabetically and has nothing to do with which
        # entity is right.
        return None, None
    subject, name = rows[0]
    return str(subject).split(":", 1)[1], str(name)


def verify(
    store: DuckStore,
    *,
    mxid: str,
    authenticated: bool,
    as_of: datetime,
) -> MatrixIdentity:
    """Establish what is known about a Matrix ID.

    ``authenticated`` is passed in rather than decided here, and must
    come from a homeserver `whoami` round trip — the caller has the
    session, this module has the rules. Defaulting it to True would let
    a typed string acquire domain verification, which is the one thing
    the MXID form is supposed to prove.
    """
    localpart, domain = parse(mxid)
    if not authenticated:
        return MatrixIdentity(
            mxid=mxid.strip(),
            localpart=localpart,
            domain=domain,
            strength=Strength.ASSERTED,
        )
    lei, name = cross_reference(store, domain, as_of=as_of)
    return MatrixIdentity(
        mxid=mxid.strip(),
        localpart=localpart,
        domain=domain,
        strength=Strength.ENTITY_CANDIDATE if lei else Strength.DOMAIN_VERIFIED,
        lei=lei,
        matched_name=name,
        verified_at=as_of,
    )


__all__ = [
    "LEGAL_NAME_FIELD",
    "MXID",
    "IdentityError",
    "MatrixIdentity",
    "Strength",
    "cross_reference",
    "parse",
    "verify",
]
