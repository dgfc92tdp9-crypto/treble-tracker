"""Moving the store, and proving it arrived before deleting anything.

The property under test is not "the files are in the new place". It is
**the source survives everything that can go wrong**. A move that verifies
after deleting is a move that loses the store on the one occasion the
verification was worth running.

The payloads are why. The derived tables can be rebuilt by replaying them;
nothing can rebuild the payloads, because they are the bytes a source
served on a day that has passed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from treble.core.datadir import MARKER_NAME, POINTER_NAME, read_marker, read_pointer, resolve
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore
from treble.store.relocate import (
    HEADROOM_BYTES,
    RelocationError,
    RelocationPlan,
    plan,
    relocate,
    tree_size,
    verify_arrival,
)

GB = 1024**3


def _populated(root: Path) -> Path:
    """A small but real store: payloads, an ingest log referencing them, and
    a fact table. Real objects, because the verification reads them back
    through the same code the workstation uses."""
    root.mkdir(parents=True, exist_ok=True)
    payloads = PayloadStore(root / "payloads")
    log = IngestLog(root / "ingest.db")
    from datetime import UTC, datetime

    for i in range(5):
        key = payloads.put(f"payload number {i}".encode())
        log.append(
            source="fred",
            payload_hash=key,
            source_uri=f"https://example.invalid/{i}",
            fetched_at=datetime(2026, 9, 1, 12, i, tzinfo=UTC),
            parser_version="1",
        )
    from treble.store.duck import DuckStore

    DuckStore(root / "treble.db")
    return root


def _plan(source: Path, target: Path, **overrides: object) -> RelocationPlan:
    """A plan with the disk measurements replaced, so the pure refusal rules
    can be tested without a disk that happens to be full."""
    base = plan(source, target)
    fields = {
        "source": base.source,
        "target": base.target,
        "bytes_to_move": base.bytes_to_move,
        "target_free": base.target_free,
        "target_holds_store": base.target_holds_store,
        **overrides,
    }
    return RelocationPlan(**fields)  # type: ignore[arg-type]


class TestRefusingBeforeAnythingMoves:
    def test_a_target_without_room_is_refused(self, tmp_path: Path) -> None:
        ready = _plan(
            _populated(tmp_path / "src"),
            tmp_path / "dst",
            bytes_to_move=10 * GB,
            target_free=5 * GB,
        )
        assert not ready.ok
        assert any("free" in problem for problem in ready.problems)

    def test_a_target_with_room_but_no_headroom_is_refused(self, tmp_path: Path) -> None:
        """A move that exactly fits lands on a disk that is full on
        arrival — the next compaction rewrites Parquet partitions and has
        nowhere to put them."""
        ready = _plan(
            _populated(tmp_path / "src"),
            tmp_path / "dst",
            bytes_to_move=10 * GB,
            target_free=10 * GB + 1,
        )
        assert not ready.ok

    def test_headroom_beyond_the_move_is_enough(self, tmp_path: Path) -> None:
        ready = _plan(
            _populated(tmp_path / "src"),
            tmp_path / "dst",
            bytes_to_move=10 * GB,
            target_free=11 * GB + HEADROOM_BYTES,
        )
        assert ready.ok, ready.problems

    def test_a_target_already_holding_a_store_is_refused(self, tmp_path: Path) -> None:
        """Copying onto another store would interleave two of them and
        leave neither trustworthy."""
        source = _populated(tmp_path / "src")
        target = _populated(tmp_path / "dst")
        assert not plan(source, target).ok

    def test_a_target_inside_the_source_is_refused(self, tmp_path: Path) -> None:
        source = _populated(tmp_path / "src")
        assert not plan(source, source / "nested").ok

    def test_moving_a_store_onto_itself_is_refused(self, tmp_path: Path) -> None:
        source = _populated(tmp_path / "src")
        assert not plan(source, source).ok

    def test_a_source_that_is_not_there_is_refused(self, tmp_path: Path) -> None:
        assert not plan(tmp_path / "nothing", tmp_path / "dst").ok

    def test_every_reason_is_reported_at_once(self, tmp_path: Path) -> None:
        """Told about the space, then the existing store, then the nesting,
        a person has run the command three times to learn one answer."""
        source = _populated(tmp_path / "src")
        ready = _plan(source, _populated(tmp_path / "dst"), target_free=0, bytes_to_move=GB)
        assert len(ready.problems) >= 2

    def test_a_refused_plan_will_not_run(self, tmp_path: Path) -> None:
        source = _populated(tmp_path / "src")
        ready = _plan(source, tmp_path / "dst", target_free=0, bytes_to_move=GB)
        with pytest.raises(RelocationError):
            relocate(ready)
        assert (source / "treble.db").exists()


class TestAGoodMove:
    def test_the_store_arrives_and_the_source_goes(self, tmp_path: Path) -> None:
        source = _populated(tmp_path / "src")
        moved = relocate(plan(source, tmp_path / "dst"))
        assert (moved / "payloads").is_dir()
        assert not (source / "treble.db").exists()

    def test_every_payload_is_readable_at_the_target(self, tmp_path: Path) -> None:
        source = _populated(tmp_path / "src")
        moved = relocate(plan(source, tmp_path / "dst"))
        store = PayloadStore(moved / "payloads")
        log = IngestLog(moved / "ingest.db")
        for entry in log.read():
            assert store.get(entry.payload_hash)

    def test_the_new_location_is_marked(self, tmp_path: Path) -> None:
        moved = relocate(plan(_populated(tmp_path / "src"), tmp_path / "dst"), label="ssd")
        marker = read_marker(moved)
        assert marker is not None and marker.label == "ssd"

    def test_the_old_location_points_at_the_new_one(self, tmp_path: Path) -> None:
        source = _populated(tmp_path / "src")
        moved = relocate(plan(source, tmp_path / "dst"))
        assert read_pointer(source) == moved

    def test_the_resolver_follows_it_without_configuration(self, tmp_path: Path) -> None:
        """The whole reason for the pointer. A store held in place by an
        environment variable reads empty the first time somebody opens a
        shell without it."""
        source = _populated(tmp_path / "src")
        moved = relocate(plan(source, tmp_path / "dst"))
        assert resolve(source) == moved

    def test_keeping_the_original_leaves_two_readable_copies(self, tmp_path: Path) -> None:
        """One external disk is capacity, not safety."""
        source = _populated(tmp_path / "src")
        moved = relocate(plan(source, tmp_path / "dst"), remove_source=False)
        assert (source / "treble.db").exists()
        assert (moved / "treble.db").exists()

    def test_keeping_the_original_leaves_no_pointer(self, tmp_path: Path) -> None:
        """A pointer in a directory that still holds a store would send the
        resolver away from a perfectly good copy."""
        source = _populated(tmp_path / "src")
        relocate(plan(source, tmp_path / "dst"), remove_source=False)
        assert not (source / POINTER_NAME).exists()
        assert resolve(source) == source


class TestVerificationCatchesABadCopy:
    """Each of these is a way a copy can go wrong that a file count misses."""

    def test_a_dropped_payload_is_caught(self, tmp_path: Path) -> None:
        source = _populated(tmp_path / "src")
        target = tmp_path / "dst"
        shutil.copytree(source, target)
        victim = next((target / "payloads").rglob("*.gz"))
        victim.unlink()
        with pytest.raises(RelocationError, match="did not arrive intact"):
            list(verify_arrival(source, target))

    def test_a_corrupted_payload_is_caught(self, tmp_path: Path) -> None:
        """Same size, different bytes — invisible to anything but the hash."""
        source = _populated(tmp_path / "src")
        target = tmp_path / "dst"
        shutil.copytree(source, target)
        victim = next((target / "payloads").rglob("*.gz"))
        victim.write_bytes(b"\x1f\x8b" + b"\x00" * (victim.stat().st_size - 2))
        with pytest.raises(RelocationError, match="did not arrive intact"):
            list(verify_arrival(source, target))

    def test_a_missing_ingest_log_is_caught(self, tmp_path: Path) -> None:
        source = _populated(tmp_path / "src")
        target = tmp_path / "dst"
        shutil.copytree(source, target)
        (target / "ingest.db").unlink()
        with pytest.raises(RelocationError):
            list(verify_arrival(source, target))

    def test_a_good_copy_passes(self, tmp_path: Path) -> None:
        """Proves the three above can fail rather than the verification
        refusing everything."""
        source = _populated(tmp_path / "src")
        target = tmp_path / "dst"
        shutil.copytree(source, target)
        assert list(verify_arrival(source, target))[-1] == "verified"

    def test_the_source_survives_a_failed_verification(self, tmp_path: Path) -> None:
        """The property the whole module exists for."""
        source = _populated(tmp_path / "src")
        target = tmp_path / "dst"
        shutil.copytree(source, target)
        next((target / "payloads").rglob("*.gz")).unlink()
        with pytest.raises(RelocationError):
            list(verify_arrival(source, target))
        assert tree_size(source) > 0
        assert PayloadStore(source / "payloads")
        log = IngestLog(source / "ingest.db")
        store = PayloadStore(source / "payloads")
        for entry in log.read():
            assert store.get(entry.payload_hash)

    def test_the_failure_says_the_source_is_untouched(self, tmp_path: Path) -> None:
        """The first thing a person needs to know when a move fails."""
        source = _populated(tmp_path / "src")
        target = tmp_path / "dst"
        shutil.copytree(source, target)
        next((target / "payloads").rglob("*.gz")).unlink()
        with pytest.raises(RelocationError, match="has not been touched"):
            list(verify_arrival(source, target))


class TestTheMarkerTravels:
    def test_a_moved_store_keeps_its_identity(self, tmp_path: Path) -> None:
        """Moving a store does not make it a different store."""
        source = _populated(tmp_path / "src")
        from treble.core.datadir import new_identity, write_marker

        original = new_identity("first home")
        write_marker(source, original)
        moved = relocate(plan(source, tmp_path / "dst"))
        assert read_marker(moved) == original
        assert (moved / MARKER_NAME).exists()


class TestTheSourceSurvivesABadMove:
    """The property the module exists for, tested through `relocate` itself.

    Found by mutation: deleting the `verify_arrival` call from `relocate`
    killed no test. The verification had thorough tests and the *wiring* had
    none — so a refactor that dropped the call would have shipped a move
    that deletes the original and checks afterwards, which is the one
    ordering that loses the store on exactly the occasion the check was
    worth running.
    """

    def _copy_then_damage(self, monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
        """Copy faithfully, then lose one payload — a real bad copy, caught
        by the real verification rather than by a stubbed one.

        `*args` because `copytree` recurses through its own module-level
        name, so this wrapper is re-entered for every subdirectory with
        seven positional arguments. Only the outermost call — the one whose
        destination is the target — does the damage.
        """
        from treble.store import relocate as module

        real = shutil.copytree

        def damaging(*args: object, **kwargs: object) -> object:
            result = real(*args, **kwargs)  # type: ignore[arg-type]
            if Path(str(args[1])) == target:
                next(target.joinpath("payloads").rglob("*.gz")).unlink()
            return result

        monkeypatch.setattr(module.shutil, "copytree", damaging)

    def test_a_failed_move_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = _populated(tmp_path / "src")
        self._copy_then_damage(monkeypatch, tmp_path / "dst")
        with pytest.raises(RelocationError, match="did not arrive intact"):
            relocate(plan(source, tmp_path / "dst"))

    def test_the_source_is_still_there(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _populated(tmp_path / "src")
        self._copy_then_damage(monkeypatch, tmp_path / "dst")
        with pytest.raises(RelocationError):
            relocate(plan(source, tmp_path / "dst"))
        assert (source / "treble.db").exists()
        assert (source / "ingest.db").exists()

    def test_every_payload_is_still_readable_from_the_source(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not merely present — readable through the path that verifies each
        against its content address."""
        source = _populated(tmp_path / "src")
        self._copy_then_damage(monkeypatch, tmp_path / "dst")
        with pytest.raises(RelocationError):
            relocate(plan(source, tmp_path / "dst"))
        store = PayloadStore(source / "payloads")
        entries = IngestLog(source / "ingest.db").read()
        assert entries
        for entry in entries:
            assert store.get(entry.payload_hash)

    def test_no_pointer_is_left_sending_anyone_at_the_bad_copy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pointer written before verification would redirect every future
        command to the incomplete store — losing the good one without ever
        deleting it."""
        source = _populated(tmp_path / "src")
        self._copy_then_damage(monkeypatch, tmp_path / "dst")
        with pytest.raises(RelocationError):
            relocate(plan(source, tmp_path / "dst"))
        assert not (source / POINTER_NAME).exists()
        assert resolve(source) == source

    def test_a_clean_move_still_completes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Proves the four above turn on the damage rather than on
        `relocate` having become unable to finish at all."""
        source = _populated(tmp_path / "src")
        moved = relocate(plan(source, tmp_path / "dst"))
        assert (moved / "treble.db").exists()
        assert not (source / "treble.db").exists()
