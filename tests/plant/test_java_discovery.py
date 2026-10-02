"""The Kafka fixture's JDK guard, and the blind spot it was rewritten to close.

The guard this tests replaced was `shutil.which("java") is None`. It passed
on a machine where **no Java could run at all**: macOS ships a `/usr/bin/java`
stub regardless of what is installed, and on 2026-10-02 it resolved to an
x86_64 JDK on an arm64 machine running macOS 27, which no longer carries
Rosetta. Every invocation died with `Bad CPU type in executable`.

Twenty-three tests errored, and the guard written specifically to stop an
unstated environment assumption did not fire — because the file it checked
for was present the whole time. That is failure mode C(ii): a condition that
could not match the fault it guards.

So these tests do not assert that the guard exists. They assert it can
**fail**, on exactly the input that defeated its predecessor: a `java` that
is there and does not run.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from conftest import _java_home, _runs


class TestRunningIsNotExisting:
    """The distinction the old guard could not draw."""

    def test_a_file_that_is_not_a_program_does_not_run(self, tmp_path: Path) -> None:
        # The shape of the real fault: `shutil.which` would find this, because
        # it is present and executable. It is not a JDK.
        fake = tmp_path / "java"
        fake.write_text("\x7fELF not really\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        assert fake.exists()
        assert os.access(fake, os.X_OK)
        assert _runs(fake) is False

    def test_a_missing_file_does_not_run(self, tmp_path: Path) -> None:
        assert _runs(tmp_path / "nothing-here") is False

    def test_the_real_jdk_runs(self) -> None:
        # Not a tautology: this is the positive half, and it is what makes the
        # negative cases above evidence rather than a broken `_runs`.
        assert _runs(_java_home() / "bin" / "java") is True


class TestTheGuardCanFail:
    def test_it_raises_when_nothing_on_the_machine_runs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        present_but_dead = tmp_path / "jdk"
        (present_but_dead / "bin").mkdir(parents=True)
        dead = present_but_dead / "bin" / "java"
        dead.write_text("#!/nonexistent/interpreter\n")
        dead.chmod(dead.stat().st_mode | stat.S_IEXEC)

        monkeypatch.setattr("conftest._JAVA_CANDIDATES", ())
        monkeypatch.setenv("JAVA_HOME", str(present_but_dead))
        monkeypatch.setenv("PATH", str(tmp_path))

        with pytest.raises(RuntimeError) as caught:
            _java_home()
        message = str(caught.value)
        # The old failure was a bare CalledProcessError from kafka-storage.sh
        # naming nothing. The remedy has to be in the error.
        assert "brew install openjdk@21" in message
        assert "Bad CPU type" in message
        assert "x86_64" in message

    def test_a_declared_java_home_that_does_not_run_is_not_believed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # JAVA_HOME is consulted first, so a stale one must not shadow a
        # working JDK further down the list — which is the state this machine
        # was in, with java_home reporting two x86_64 JDKs and nothing else.
        monkeypatch.setenv("JAVA_HOME", str(tmp_path / "not-a-jdk"))
        assert _runs(_java_home() / "bin" / "java") is True
