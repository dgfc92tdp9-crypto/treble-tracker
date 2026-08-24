"""The people directory, and the three things it must not get wrong.

A leak here is different from a wrong number elsewhere: a price that is
wrong can be corrected, and a phone number that reached a federated peer
cannot be recalled. So visibility is tested as a property of the
directory rather than of any screen.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from treble.people.directory import (
    DEFAULT_VISIBILITY,
    Directory,
    DirectoryError,
    Verification,
    Visibility,
)

NOW = datetime(2026, 8, 24, tzinfo=UTC)

FULL = {
    "display_name": "Jane Roe",
    "employer": "Acme Asset Management",
    "role": "Credit analyst",
    "coverage": "European high yield",
    "email": "jane@example.invalid",
    "phone": "+44 7700 900000",
    "biography": "Twelve years in credit.",
}


def _directory(tmp_path: Path, **profiles: dict[str, str]) -> Directory:
    directory = Directory(tmp_path)
    for handle, values in profiles.items():
        directory.put(handle, values=values, now=NOW)
    return directory


class TestVisibilityIsEnforcedAtRead:
    """Not at render. Filtering in the renderer would put the guarantee
    in the last layer instead of the first, which is where every leak of
    this kind comes from."""

    def test_a_private_field_never_leaves_the_directory(self, tmp_path: Path) -> None:
        directory = _directory(tmp_path, jane=FULL)
        for level in (Visibility.PUBLIC, Visibility.NETWORK):
            shown = directory.get("jane").visible(to=level)
            assert "phone" not in shown, f"private field leaked to {level.value}"

    def test_a_network_field_is_withheld_from_the_public(self, tmp_path: Path) -> None:
        shown = _directory(tmp_path, jane=FULL).get("jane").visible(to=Visibility.PUBLIC)
        assert "email" not in shown
        assert "employer" in shown

    def test_a_verified_member_sees_network_fields(self, tmp_path: Path) -> None:
        shown = _directory(tmp_path, jane=FULL).get("jane").visible(to=Visibility.NETWORK)
        assert shown["email"] == "jane@example.invalid"

    def test_the_person_can_see_their_own_private_fields(self, tmp_path: Path) -> None:
        """Through a method that names what it is doing, so no caller
        reaches private data by accident."""
        assert _directory(tmp_path, jane=FULL).own_view("jane")["phone"] == "+44 7700 900000"

    def test_a_person_may_open_a_field_the_default_closes(self, tmp_path: Path) -> None:
        """Users control their own visibility — the defaults are defaults."""
        directory = Directory(tmp_path)
        directory.put("jane", values=FULL, visibility={"phone": Visibility.PUBLIC}, now=NOW)
        assert "phone" in directory.get("jane").visible(to=Visibility.PUBLIC)

    def test_a_person_may_close_a_field_the_default_opens(self, tmp_path: Path) -> None:
        directory = Directory(tmp_path)
        directory.put("jane", values=FULL, visibility={"employer": Visibility.PRIVATE}, now=NOW)
        assert "employer" not in directory.get("jane").visible(to=Visibility.NETWORK)

    def test_contact_details_are_not_public_by_default(self, tmp_path: Path) -> None:
        """A directory publishing everyone's email to an unauthenticated
        reader is a harvesting target. The spec says users control
        visibility, not that everything is open."""
        assert DEFAULT_VISIBILITY["email"] is Visibility.NETWORK
        assert DEFAULT_VISIBILITY["phone"] is Visibility.PRIVATE


class TestSearchDoesNotLeakByInference:
    def test_a_private_field_does_not_make_a_profile_match(self, tmp_path: Path) -> None:
        """A searcher who learns that a term matches somebody has learned
        a private field's value without ever being shown it."""
        directory = _directory(tmp_path, jane=FULL)
        assert directory.search("7700", to=Visibility.PUBLIC) == ()

    def test_a_network_field_does_not_match_for_the_public(self, tmp_path: Path) -> None:
        directory = _directory(tmp_path, jane=FULL)
        assert directory.search("jane@example.invalid", to=Visibility.PUBLIC) == ()
        assert len(directory.search("jane@example.invalid", to=Visibility.NETWORK)) == 1

    def test_a_public_field_matches(self, tmp_path: Path) -> None:
        directory = _directory(tmp_path, jane=FULL)
        ((profile, shown),) = directory.search("high yield")
        assert profile.handle == "jane"
        assert "phone" not in shown

    def test_an_empty_query_lists_everyone(self, tmp_path: Path) -> None:
        directory = _directory(tmp_path, jane=FULL, john=dict(FULL, display_name="John Doe"))
        assert [p.handle for p, _ in directory.search()] == ["jane", "john"]

    def test_results_are_sorted(self, tmp_path: Path) -> None:
        """Two runs of one search must return the same order, like every
        other listing in this system."""
        directory = _directory(tmp_path, zoe=FULL, adam=FULL, mary=FULL)
        assert [p.handle for p, _ in directory.search()] == ["adam", "mary", "zoe"]


class TestVerificationIsRecordedNeverInferred:
    def test_a_new_profile_is_self_asserted(self, tmp_path: Path) -> None:
        """Nothing on this install checks an identity, so the default
        must be the one that claims nothing."""
        assert _directory(tmp_path, jane=FULL).get("jane").verification is (
            Verification.SELF_ASSERTED
        )

    def test_self_assertion_does_not_count_as_verified(self, tmp_path: Path) -> None:
        assert not Verification.SELF_ASSERTED.is_verified
        assert Verification.MATRIX.is_verified
        assert Verification.CREDENTIAL.is_verified

    def test_a_caller_that_verified_an_identity_may_say_so(self, tmp_path: Path) -> None:
        """The argument exists so a homeserver or credential verifier can
        record what it actually established — not so a profile can assert
        it about itself."""
        directory = Directory(tmp_path)
        directory.put("jane", values=FULL, verification=Verification.MATRIX, now=NOW)
        assert directory.get("jane").verification.is_verified


class TestErasureIsReal:
    """Unlike the fact store, which is append-only by construction (I2)."""

    def test_forget_removes_the_profile(self, tmp_path: Path) -> None:
        directory = _directory(tmp_path, jane=FULL)
        directory.forget("jane")
        assert len(directory) == 0
        with pytest.raises(DirectoryError, match="no profile"):
            directory.get("jane")

    def test_forget_survives_a_reload(self, tmp_path: Path) -> None:
        """Erasure that only held in memory would be no erasure at all."""
        _directory(tmp_path, jane=FULL).forget("jane")
        assert len(Directory(tmp_path)) == 0

    def test_the_bytes_are_gone_from_disk(self, tmp_path: Path) -> None:
        """A right to erasure is not satisfied by a tombstone that still
        carries the data."""
        _directory(tmp_path, jane=FULL).forget("jane")
        on_disk = (tmp_path / "people.json").read_text()
        assert "7700 900000" not in on_disk
        assert "jane@example.invalid" not in on_disk

    def test_forgetting_a_stranger_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(DirectoryError, match="no profile"):
            Directory(tmp_path).forget("nobody")


class TestTheProfileShapeIsClosed:
    def test_an_unknown_field_is_refused(self, tmp_path: Path) -> None:
        """An open shape would let a caller invent a field with no
        declared visibility, which would default to *something* and be a
        leak waiting on whichever default was chosen."""
        with pytest.raises(DirectoryError, match="unknown profile field"):
            Directory(tmp_path).put("jane", values={"salary": "300000"}, now=NOW)

    def test_visibility_for_an_unknown_field_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(DirectoryError, match="unknown field"):
            Directory(tmp_path).put(
                "jane",
                values={"display_name": "J"},
                visibility={"salary": Visibility.PUBLIC},  # type: ignore[dict-item]
                now=NOW,
            )

    def test_a_blank_handle_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(DirectoryError, match="needs a handle"):
            Directory(tmp_path).put("   ", values={}, now=NOW)

    def test_every_field_has_a_declared_default_visibility(self) -> None:
        """The guard for a field added to PROFILE_FIELDS and not given a
        default, which would raise on first read rather than at review."""
        from treble.people.directory import PROFILE_FIELDS

        assert set(PROFILE_FIELDS) == set(DEFAULT_VISIBILITY)


class TestPersistence:
    def test_a_profile_round_trips(self, tmp_path: Path) -> None:
        directory = Directory(tmp_path)
        directory.put(
            "jane",
            values=FULL,
            visibility={"phone": Visibility.NETWORK},
            verification=Verification.CREDENTIAL,
            now=NOW,
        )
        reloaded = Directory(tmp_path).get("jane")
        assert reloaded.values == FULL
        assert reloaded.visibility["phone"] is Visibility.NETWORK
        assert reloaded.verification is Verification.CREDENTIAL

    def test_an_unknown_file_version_is_refused(self, tmp_path: Path) -> None:
        """Rather than read a document whose shape this code does not
        know, and silently drop the fields it does not recognise."""
        (tmp_path / "people.json").write_text('{"version": 99, "profiles": []}')
        with pytest.raises(DirectoryError, match="version"):
            Directory(tmp_path)

    def test_an_absent_file_is_an_empty_directory(self, tmp_path: Path) -> None:
        assert len(Directory(tmp_path)) == 0
