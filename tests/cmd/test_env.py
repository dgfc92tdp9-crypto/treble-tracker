"""`.env` loading (spec §23.3).

The defect this prevents: credentials present on disk, application refusing
to start because nothing read them.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from treble.cmd.env import load_env, parse_env_file


class TestParse:
    def test_parses_simple_pairs(self) -> None:
        assert parse_env_file("A=1\nB=two\n") == {"A": "1", "B": "two"}

    def test_ignores_comments_and_blanks(self) -> None:
        assert parse_env_file("# note\n\nA=1\n") == {"A": "1"}

    def test_strips_surrounding_quotes(self) -> None:
        assert parse_env_file("A=\"quoted\"\nB='single'\n") == {"A": "quoted", "B": "single"}

    def test_malformed_lines_do_not_raise(self) -> None:
        # A stray line must not stop the workstation opening.
        assert parse_env_file("garbage\nA=1\n") == {"A": "1"}

    def test_values_containing_equals_survive(self) -> None:
        assert parse_env_file("TOKEN=abc=def==\n") == {"TOKEN": "abc=def=="}


class TestLoad:
    def test_applies_keys_from_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TREBLE_TEST_KEY", raising=False)
        (tmp_path / ".env").write_text("TREBLE_TEST_KEY=value\n")
        assert load_env(tmp_path) == 1
        assert os.environ["TREBLE_TEST_KEY"] == "value"

    def test_explicit_environment_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # .env is a convenience, never an override of what the operator set.
        monkeypatch.setenv("TREBLE_TEST_KEY", "from-environment")
        (tmp_path / ".env").write_text("TREBLE_TEST_KEY=from-file\n")
        load_env(tmp_path)
        assert os.environ["TREBLE_TEST_KEY"] == "from-environment"

    def test_finds_env_in_a_parent_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TREBLE_TEST_PARENT", raising=False)
        (tmp_path / ".env").write_text("TREBLE_TEST_PARENT=yes\n")
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert load_env(nested) == 1

    def test_missing_file_is_not_an_error(self, tmp_path: Path) -> None:
        assert load_env(tmp_path / "nowhere") == 0

    def test_returns_a_count_not_the_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Secrets must never reach a terminal or CI log.
        monkeypatch.delenv("TREBLE_SECRET", raising=False)
        (tmp_path / ".env").write_text("TREBLE_SECRET=hunter2\n")
        assert load_env(tmp_path) == 1
