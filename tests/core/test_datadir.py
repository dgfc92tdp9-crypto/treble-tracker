"""Refusing to open something that is not the store.

`cmd/paths.py` records this failure once already: a relative data path meant
launching from the wrong directory "silently created a fresh empty store and
rendered a screen of honest-looking dashes with no error". Anchoring the path
fixed the cause it had then. Moving the store to another disk gives it two
new ones — an unmounted volume, and nobody having told the workstation where
the bytes went — and neither is fixed by anchoring anything.

The tests that matter are the ones asserting a *refusal*. A guard that
adopts too eagerly is the bug it was written to prevent.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from treble.core.datadir import (
    MARKER_NAME,
    POINTER_NAME,
    StoreIdentity,
    StoreLocationError,
    holds_data,
    new_identity,
    read_marker,
    resolve,
    verify,
    write_marker,
    write_pointer,
)


def _store(path: Path) -> Path:
    """A directory that looks like a store, with no marker."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "treble.db").write_bytes(b"not really a database")
    return path


class TestTheMarker:
    def test_a_written_marker_reads_back(self, tmp_path: Path) -> None:
        identity = new_identity("external ssd")
        write_marker(tmp_path, identity)
        assert read_marker(tmp_path) == identity

    def test_a_directory_without_one_has_none(self, tmp_path: Path) -> None:
        assert read_marker(tmp_path) is None

    def test_a_truncated_marker_reads_as_absent(self, tmp_path: Path) -> None:
        """An interrupted write must not lock anyone out of a store whose
        data is perfectly intact. The recovery for a bad marker is to write
        a good one."""
        (tmp_path / MARKER_NAME).write_text('{"store_id": "abc"')
        assert read_marker(tmp_path) is None

    def test_two_stores_have_different_ids(self) -> None:
        assert new_identity().store_id != new_identity().store_id

    def test_the_marker_is_readable_without_this_package(self, tmp_path: Path) -> None:
        """Someone holding an unfamiliar disk should be able to tell what is
        on it with `cat`."""
        write_marker(tmp_path, new_identity("desk"))
        data = json.loads((tmp_path / MARKER_NAME).read_text())
        assert data["label"] == "desk"
        assert "store_id" in data and "created_at" in data


class TestRecognisingAStore:
    def test_a_directory_holding_a_database_holds_data(self, tmp_path: Path) -> None:
        assert holds_data(_store(tmp_path))

    def test_an_empty_directory_does_not(self, tmp_path: Path) -> None:
        assert not holds_data(tmp_path)

    def test_a_directory_of_unrelated_files_does_not(self, tmp_path: Path) -> None:
        """The case that matters: a volume that mounted, or a path someone
        else created. Treating it as a store is how an empty one gets
        built on top of it."""
        (tmp_path / "Photos").mkdir()
        (tmp_path / "notes.txt").write_text("hello")
        assert not holds_data(tmp_path)


class TestFollowingAPointer:
    def test_it_resolves_to_the_new_location(self, tmp_path: Path) -> None:
        old, new = tmp_path / "old", _store(tmp_path / "new")
        write_pointer(old, new, new_identity())
        assert resolve(old) == new

    def test_a_directory_without_one_resolves_to_itself(self, tmp_path: Path) -> None:
        assert resolve(tmp_path) == tmp_path

    def test_a_chain_of_moves_is_followed(self, tmp_path: Path) -> None:
        """A store moved twice is still one store."""
        first, second, third = tmp_path / "a", tmp_path / "b", _store(tmp_path / "c")
        write_pointer(second, third, new_identity())
        write_pointer(first, second, new_identity())
        assert resolve(first) == third

    def test_a_loop_raises_rather_than_hanging(self, tmp_path: Path) -> None:
        """A corrupted signpost. Spinning forever is a worse way to report
        it than saying so."""
        a, b = tmp_path / "a", tmp_path / "b"
        write_pointer(a, b, new_identity())
        write_pointer(b, a, new_identity())
        with pytest.raises(StoreLocationError, match="loop"):
            resolve(a)

    def test_a_pointer_to_itself_raises(self, tmp_path: Path) -> None:
        write_pointer(tmp_path, tmp_path, new_identity())
        with pytest.raises(StoreLocationError, match="loop"):
            resolve(tmp_path)

    def test_the_pointer_says_what_it_is_in_plain_words(self, tmp_path: Path) -> None:
        """Someone finding an apparently empty data directory should learn
        where everything went from the file itself."""
        write_pointer(tmp_path, tmp_path / "elsewhere", new_identity())
        note = json.loads((tmp_path / POINTER_NAME).read_text())
        assert "moved" in note["note"].lower()
        assert note["moved_to"].endswith("elsewhere")


class TestVerify:
    def test_a_real_store_passes(self, tmp_path: Path) -> None:
        verify(_store(tmp_path))

    def test_a_marked_but_empty_store_passes(self, tmp_path: Path) -> None:
        """A store created and not yet populated is legitimate."""
        write_marker(tmp_path, new_identity())
        verify(tmp_path)

    def test_a_fresh_default_directory_passes(self, tmp_path: Path) -> None:
        """First run on a new checkout creates its store. The guard is about
        directories that were *expected* to hold one."""
        verify(tmp_path / "not-created-yet")

    def test_a_move_to_somewhere_absent_is_refused(self, tmp_path: Path) -> None:
        """The unmounted volume. This is the whole point."""
        origin, target = tmp_path / "old", Path("/Volumes/NotMounted/treble")
        write_pointer(origin, target, new_identity())
        with pytest.raises(StoreLocationError, match="mounted"):
            verify(target, origin=origin)

    def test_the_refusal_names_both_ends_and_the_pointer(self, tmp_path: Path) -> None:
        """ "The store is missing" is a puzzle. "It was moved to X, X is not
        there, the pointer is at Y" is a fix."""
        origin, target = tmp_path / "old", tmp_path / "gone"
        write_pointer(origin, target, new_identity())
        with pytest.raises(StoreLocationError) as caught:
            verify(target, origin=origin)
        message = str(caught.value)
        assert str(target) in message
        assert str(origin / POINTER_NAME) in message

    def test_a_directory_named_outright_is_not_a_relocation(self, tmp_path: Path) -> None:
        """`--data-dir /somewhere/new` on a first run. Without a pointer
        leading there, nothing was moved, and telling the caller their store
        "was moved" to a path they just typed would be the guard inventing a
        history.

        Found by the CLI suite, which passes a temporary directory to almost
        every command and went red on all of them.
        """
        verify(tmp_path / "brand-new", origin=tmp_path / "default")

    def test_a_pointer_leading_elsewhere_says_nothing_about_this_request(
        self, tmp_path: Path
    ) -> None:
        """A real pointer at the default path, and a caller naming a
        different directory outright. The pointer exists but does not lead
        here, so it is not this request's business."""
        origin = tmp_path / "default"
        write_pointer(origin, tmp_path / "somewhere-else", new_identity())
        verify(tmp_path / "unrelated", origin=origin)

    def test_an_explicit_data_dir_that_is_absent_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Setting TREBLE_DATA_DIR is a statement that the store is there.
        Creating a new empty one instead answers a question nobody asked."""
        missing = tmp_path / "not-there"
        monkeypatch.setenv("TREBLE_DATA_DIR", str(missing))
        with pytest.raises(StoreLocationError, match="mounted"):
            verify(missing)

    def test_without_the_variable_the_same_path_is_allowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Proves the assertion above turns on the variable rather than on
        the directory being absent — otherwise every first run would fail."""
        monkeypatch.delenv("TREBLE_DATA_DIR", raising=False)
        verify(tmp_path / "not-there")

    def test_an_unmarked_store_is_adopted_not_refused(self, tmp_path: Path) -> None:
        """Every install predating the marker is in this state. A guard that
        locked those out would do more harm than the fault it guards."""
        verify(_store(tmp_path))


class TestTheIdentityRoundTrips:
    def test_created_at_keeps_its_timezone(self) -> None:
        identity = StoreIdentity(
            store_id="abc", created_at=datetime(2026, 9, 1, tzinfo=UTC), label="x"
        )
        assert StoreIdentity.from_json(identity.to_json()) == identity
