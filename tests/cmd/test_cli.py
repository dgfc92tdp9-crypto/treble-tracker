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

    def test_discovery_universe_reports_not_wired_yet(self, tmp_path: Path) -> None:
        # Honest failure rather than a silent no-op: the full universe needs
        # run-time filer discovery, which is the remaining WP7 piece.
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
            ],
        )
        assert result.exit_code == 2
        assert "discovery" in result.stdout.lower() or "not wired" in result.stdout.lower()

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
