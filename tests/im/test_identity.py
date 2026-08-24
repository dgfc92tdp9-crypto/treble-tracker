"""What a Matrix ID proves, and the three links that prove different things.

The spec asks for "verified identity … via domain control and LEI
cross-reference". That is a chain, and the strength `PEOP` prints is the
strength of the weakest link. Every test here is about not collapsing
them into one word.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.im.identity import IdentityError, Strength, cross_reference, parse, verify
from treble.store.duck import DuckStore

NOW = datetime(2026, 8, 24, tzinfo=UTC)


def _store(tmp_path: Path, entities: dict[str, str]) -> DuckStore:
    store = DuckStore(tmp_path / "i.db")
    record = Provenance(
        source_system="gleif",
        source_uri="https://example.invalid/gleif",
        retrieved_at=NOW,
        method=ExtractionMethod.BULK_FILE,
        extractor_version="1",
        payload_hash="a" * 64,
    )
    store.write_provenance([record])
    store.write_facts(
        [
            Fact(
                subject=TUID(f"lei:{lei}"),
                field="gleif:legalName",
                value=name,
                effective_from=date(2026, 1, 1),
                effective_to=None,
                knowledge_from=NOW,
                provenance_id=record.id,
            )
            for lei, name in entities.items()
        ]
    )
    return store


class TestParsingRefusesNonIdentities:
    @pytest.mark.parametrize(
        "bad",
        ["jane:acme.com", "@jane", "@jane@acme.com", "", "  ", "not-an-id @jane:acme.com"],
    )
    def test_a_string_that_is_not_an_mxid_is_refused(self, bad: str) -> None:
        """Refused rather than parsed into a partial: an identity that
        fails to parse is not an identity with an empty domain, and
        treating it as one puts a blank employer beside a real name."""
        with pytest.raises(IdentityError):
            parse(bad)

    def test_a_trailing_string_cannot_smuggle_an_identity(self) -> None:
        """The pattern is anchored. An unanchored one would accept
        `evil @jane:acme.com` and read the employer out of the middle."""
        with pytest.raises(IdentityError):
            parse("@jane:acme.com evil")

    def test_a_valid_id_splits(self) -> None:
        assert parse("@jane:acme.com") == ("jane", "acme.com")

    def test_a_port_is_part_of_the_server_name(self) -> None:
        assert parse("@jane:acme.com:8448")[1] == "acme.com:8448"


class TestTheChainDoesNotCollapse:
    def test_an_unauthenticated_id_proves_nothing(self, tmp_path: Path) -> None:
        """Typing a string must not acquire domain verification — the one
        thing the MXID form is supposed to prove."""
        store = _store(tmp_path, {})
        identity = verify(store, mxid="@jane:acme.com", authenticated=False, as_of=NOW)
        assert identity.strength is Strength.ASSERTED
        assert "nothing checked" in identity.describe()

    def test_authentication_grants_domain_verification(self, tmp_path: Path) -> None:
        """You cannot obtain @anyone:acme.com unless acme.com's
        homeserver issues it."""
        store = _store(tmp_path, {})
        identity = verify(store, mxid="@jane:acme.com", authenticated=True, as_of=NOW)
        assert identity.strength is Strength.DOMAIN_VERIFIED
        assert "no GLEIF entity matched" in identity.describe()

    def test_an_entity_match_is_reported_as_a_candidate(self, tmp_path: Path) -> None:
        store = _store(tmp_path, {"5493008HSF8L4M2LIJ82": "KENVUE INC."})
        identity = verify(store, mxid="@jane:kenvue.com", authenticated=True, as_of=NOW)
        assert identity.strength is Strength.ENTITY_CANDIDATE
        assert identity.lei == "5493008HSF8L4M2LIJ82"
        assert "unconfirmed" in identity.describe(), "a candidate must not read as settled"

    def test_the_matched_name_is_published(self, tmp_path: Path) -> None:
        """So a reader judges the match rather than trusting the word."""
        store = _store(tmp_path, {"5493008HSF8L4M2LIJ82": "KENVUE INC."})
        identity = verify(store, mxid="@jane:kenvue.com", authenticated=True, as_of=NOW)
        assert "KENVUE INC." in identity.describe()

    def test_a_coincidental_match_is_visible_rather_than_hidden(self, tmp_path: Path) -> None:
        """`old.com` matches "Old Dominion Freight Line, Inc." and has
        nothing to do with it. The design does not prevent this — no
        name-based rule can — it makes it legible, which is why the name
        appears and the word says unconfirmed."""
        store = _store(tmp_path, {"5299009TWK32WE417T96": "Old Dominion Freight Line, Inc."})
        identity = verify(store, mxid="@jane:old.com", authenticated=True, as_of=NOW)
        assert identity.strength is Strength.ENTITY_CANDIDATE
        assert "Old Dominion Freight Line, Inc." in identity.describe()
        assert "unconfirmed" in identity.describe()


class TestTheCrossReference:
    def test_it_matches_the_first_word_not_the_whole_name(self, tmp_path: Path) -> None:
        """The first version compared the domain label against the entire
        legal name — `KENVUE` against `KENVUE INC.` — so it could never
        match anything. A lookup incapable of firing."""
        store = _store(tmp_path, {"L1": "KENVUE INC."})
        assert cross_reference(store, "kenvue.com", as_of=NOW) == ("L1", "KENVUE INC.")

    def test_a_comma_does_not_defeat_the_first_word(self, tmp_path: Path) -> None:
        store = _store(tmp_path, {"L1": "Uber Technologies, Inc."})
        assert cross_reference(store, "uber.com", as_of=NOW)[0] == "L1"

    def test_an_ambiguous_match_is_no_match(self, tmp_path: Path) -> None:
        """Two entities sharing a first word return nothing rather than
        the alphabetically first, which has nothing to do with which is
        right."""
        store = _store(tmp_path, {"L1": "Acme Corp", "L2": "Acme Holdings PLC"})
        assert cross_reference(store, "acme.com", as_of=NOW) == (None, None)

    def test_an_unknown_domain_is_not_found_rather_than_an_error(self, tmp_path: Path) -> None:
        """The entity may simply not be in the store — 4 legal names on
        the live install — and that is not a verification failure."""
        store = _store(tmp_path, {"L1": "KENVUE INC."})
        assert cross_reference(store, "nowhere.example", as_of=NOW) == (None, None)

    def test_a_port_does_not_defeat_the_lookup(self, tmp_path: Path) -> None:
        store = _store(tmp_path, {"L1": "KENVUE INC."})
        assert cross_reference(store, "kenvue.com:8448", as_of=NOW)[0] == "L1"

    def test_the_match_is_point_in_time(self, tmp_path: Path) -> None:
        """Like every other read (I2): an entity learned about after the
        as-of date is not evidence at that date."""
        store = _store(tmp_path, {"L1": "KENVUE INC."})
        earlier = datetime(2026, 1, 1, tzinfo=UTC)
        assert cross_reference(store, "kenvue.com", as_of=earlier) == (None, None)


class TestTheDomainIsTheEmployer:
    def test_a_port_is_stripped_for_display(self, tmp_path: Path) -> None:
        """A homeserver may run on a port, and acme.com:8448 is the same
        employer as acme.com."""
        store = _store(tmp_path, {})
        identity = verify(store, mxid="@jane:acme.com:8448", authenticated=True, as_of=NOW)
        assert identity.employer_domain == "acme.com"

    def test_an_ipv6_literal_is_left_alone(self, tmp_path: Path) -> None:
        """Splitting on the first colon would turn `[::1]` into `[`."""
        store = _store(tmp_path, {})
        identity = verify(store, mxid="@jane:[::1]", authenticated=True, as_of=NOW)
        assert identity.employer_domain == "[::1]"
