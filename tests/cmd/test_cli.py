"""CLI behaviour (spec §4). Offline: no command under test touches the network.

The contact-email guard is the important one — EDGAR blocks unidentified
requests, sometimes at the IP level (CLAUDE.md §6), so the CLI must refuse
to start a run without one rather than discovering it mid-fetch.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from treble.cmd.cli import ContactMissingError, _contact_email, app

runner = CliRunner()
CONFIG = Path(__file__).parent.parent.parent / "config" / "universe.yaml"


class TestContactGuard:
    def test_missing_contact_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TREBLE_EDGAR_CONTACT", raising=False)
        with pytest.raises(ContactMissingError, match="EDGAR"):
            _contact_email(None)

    def test_malformed_contact_is_refused(self) -> None:
        with pytest.raises(ContactMissingError):
            _contact_email("not-an-email")

    def test_environment_variable_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TREBLE_EDGAR_CONTACT", "someone@example.com")
        assert _contact_email(None) == "someone@example.com"

    def test_explicit_argument_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TREBLE_EDGAR_CONTACT", "env@example.com")
        assert _contact_email("arg@example.com") == "arg@example.com"


class TestUniverses:
    def test_lists_configured_universes(self) -> None:
        result = runner.invoke(app, ["universes", "--config", str(CONFIG)])
        assert result.exit_code == 0
        assert "dev" in result.stdout
        assert "full" in result.stdout


class TestPopulateDryRun:
    def test_dry_run_reports_without_fetching(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "populate",
                "--universe",
                "dev",
                "--config",
                str(CONFIG),
                "--data-dir",
                str(tmp_path / "data"),
                "--contact",
                "test@example.com",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "outstanding steps" in result.stdout

    def test_discovery_universe_resolves_filers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The full universe resolves its filer list at run time (decision
        0005). Discovery is stubbed here — the parser itself is covered by
        the recorded-fixture test in tests/ingest/test_populate.py."""
        from treble.ingest.populate import Populator

        monkeypatch.setattr(Populator, "discover_ciks", lambda self: (51143, 320193))
        result = runner.invoke(
            app,
            [
                "populate",
                "--universe",
                "full",
                "--config",
                str(CONFIG),
                "--data-dir",
                str(tmp_path / "data"),
                "--contact",
                "test@example.com",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "discovered 2 filers" in result.stdout
        # Two EDGAR steps per filer, plus macro and Treasury steps.
        assert "outstanding steps" in result.stdout

    def test_unknown_universe_fails_clearly(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "populate",
                "--universe",
                "nonexistent",
                "--config",
                str(CONFIG),
                "--data-dir",
                str(tmp_path / "data"),
                "--contact",
                "test@example.com",
            ],
        )
        assert result.exit_code != 0


class TestStatus:
    def test_status_on_empty_install(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "status",
                "--universe",
                "dev",
                "--config",
                str(CONFIG),
                "--data-dir",
                str(tmp_path / "data"),
                "--contact",
                "test@example.com",
            ],
        )
        assert result.exit_code == 0
        assert "nothing ingested yet" in result.stdout

    def test_status_creates_no_partial_state_on_read(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        runner.invoke(
            app,
            [
                "status",
                "--universe",
                "dev",
                "--config",
                str(CONFIG),
                "--data-dir",
                str(data_dir),
                "--contact",
                "test@example.com",
            ],
        )
        # Opening the stores is enough to create them; nothing should have
        # been fetched.
        from treble.store.ingest_log import IngestLog

        assert IngestLog(data_dir / "ingest.db").read() == []


class TestEveryDocumentedCommandIsRegistered:
    """A command can stop existing without any other test noticing.

    While the runway reporting was being added, a helper was inserted
    between `@app.command()` and `def storage(...)`. The decorator bound to
    the helper, `treble storage` ceased to exist, a `_report_runway`
    command appeared in its place — and `make gate` stayed green: lint,
    types, 90% coverage, every structural check. Nothing asserted the CLI's
    surface, so the only way to find it was to run the command.

    Listed explicitly rather than derived from the app, which would compare
    the registry against itself and pass whatever it contained.
    """

    #: Every command `treble --help` is expected to offer. Adding one is a
    #: deliberate act; losing one should not be a quiet one.
    EXPECTED = frozenset(
        {
            "populate",
            "init",
            "addin",
            "status",
            "refresh",
            "tui",
            "serve",
            "universes",
            "compact",
            "replay",
            "storage",
            "homeserver",
            "simulator",
        }
    )

    def _registered(self) -> set[str]:
        return {
            command.name or (command.callback.__name__ if command.callback else "")
            for command in app.registered_commands
        }

    def test_no_command_has_gone_missing(self) -> None:
        assert self._registered() >= self.EXPECTED

    def test_no_private_helper_became_a_command(self) -> None:
        """The other half of the same accident: the helper that stole the
        decorator was registered as a user-facing command called
        `_report_runway`."""
        private = {name for name in self._registered() if name.startswith("_")}
        assert not private, f"private helpers exposed as commands: {sorted(private)}"

    def test_the_help_text_lists_them(self) -> None:
        """End to end through Typer, so a command that is registered but
        unreachable still fails."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for name in sorted(self.EXPECTED):
            assert name in result.output, f"{name} is missing from `treble --help`"
