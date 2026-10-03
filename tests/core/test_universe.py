"""Universe config and resumable population planning (spec §9.4, decision 0005).

Tests the real `config/universe.yaml` rather than a synthetic fixture: a
config that parses in a test but not in production is worthless.
"""

from pathlib import Path

import pytest

from treble.core.universe import (
    PopulationStep,
    UniverseSpec,
    completion_key,
    load_universe_config,
    plan_steps,
    remaining_steps,
)

CONFIG = Path(__file__).parent.parent.parent / "config" / "universe.yaml"


class TestConfig:
    def test_real_config_parses(self) -> None:
        config = load_universe_config(CONFIG)
        assert {"dev", "full"} <= set(config.universes)

    def test_dev_universe_is_enumerated_and_small(self) -> None:
        dev = load_universe_config(CONFIG).get("dev")
        assert not dev.discovers_filers
        assert 5 <= len(dev.edgar_ciks) <= 50, "dev must stay fast to run"
        assert 51143 in dev.edgar_ciks, "IBM anchors the recorded fixtures"

    def test_full_universe_discovers_rather_than_enumerating(self) -> None:
        # Decision 0005: ~8k filers. An enumerated list would go stale the
        # day it was written, so the full universe resolves at run time.
        full = load_universe_config(CONFIG).get("full")
        assert full.discovers_filers

    def test_rate_limits_match_published_source_limits(self) -> None:
        limits = load_universe_config(CONFIG).rate_limits
        assert limits.edgar_per_second == 10.0  # SEC published limit
        assert limits.openfigi_per_minute == 25.0  # unauthenticated tier

    def test_unknown_universe_names_are_an_error(self) -> None:
        with pytest.raises(KeyError, match="available"):
            load_universe_config(CONFIG).get("nonexistent")


class TestPlanning:
    def test_plan_covers_every_configured_source(self) -> None:
        dev = load_universe_config(CONFIG).get("dev")
        steps = plan_steps(dev)
        sources = {s.source_id for s in steps}
        assert {"edgar-companyfacts", "edgar-submissions", "fred", "treasury-auctions"} <= sources
        # Two EDGAR steps per filer, one per FRED series.
        assert sum(1 for s in steps if s.source_id == "edgar-companyfacts") == len(dev.edgar_ciks)
        assert sum(1 for s in steps if s.source_id == "fred") == len(dev.fred_series)

    def test_discovery_universe_requires_supplied_ciks(self) -> None:
        full = load_universe_config(CONFIG).get("full")
        with pytest.raises(ValueError, match="discovery"):
            plan_steps(full)

    def test_discovered_ciks_are_used(self) -> None:
        full = load_universe_config(CONFIG).get("full")
        steps = plan_steps(full, discovered_ciks=(51143, 320193))
        assert sum(1 for s in steps if s.source_id == "edgar-companyfacts") == 2

    def test_plan_is_deterministic(self) -> None:
        dev = load_universe_config(CONFIG).get("dev")
        assert plan_steps(dev) == plan_steps(dev)


class TestResumability:
    """Planning is pure: `done` is supplied by the caller (which reads the
    ingest log — core may not import store, I7)."""

    def test_completed_work_is_skipped_on_rerun(self) -> None:
        steps = [
            PopulationStep(source_id="fred", key="SOFR"),
            PopulationStep(source_id="fred", key="DGS10"),
        ]
        uri_for = {
            "fred:SOFR": "https://fred.example/SOFR",
            "fred:DGS10": "https://fred.example/DGS10",
        }
        assert len(remaining_steps(steps, set(), uri_for)) == 2

        done = {completion_key("fred", uri_for["fred:SOFR"])}
        assert [s.key for s in remaining_steps(steps, done, uri_for)] == ["DGS10"]

    def test_nothing_remains_once_all_done(self) -> None:
        steps = [PopulationStep(source_id="fred", key="SOFR")]
        uri_for = {"fred:SOFR": "https://fred.example/SOFR"}
        done = {completion_key("fred", uri_for["fred:SOFR"])}
        assert remaining_steps(steps, done, uri_for) == []

    def test_same_key_different_source_is_not_confused(self) -> None:
        # A CIK appears in both EDGAR adapters; completing one must not
        # mark the other done.
        steps = [
            PopulationStep(source_id="edgar-companyfacts", key="51143"),
            PopulationStep(source_id="edgar-submissions", key="51143"),
        ]
        uri_for = {
            "edgar-companyfacts:51143": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000051143.json",
            "edgar-submissions:51143": "https://data.sec.gov/submissions/CIK0000051143.json",
        }
        done = {completion_key("edgar-companyfacts", uri_for["edgar-companyfacts:51143"])}
        remaining = remaining_steps(steps, done, uri_for)
        assert [s.source_id for s in remaining] == ["edgar-submissions"]


def test_spec_is_immutable() -> None:
    from pydantic import ValidationError

    dev = load_universe_config(CONFIG).get("dev")
    with pytest.raises(ValidationError):
        dev.name = "changed"  # type: ignore[misc]


def test_universe_spec_rejects_bad_shape() -> None:
    from pydantic import ValidationError

    # Only the literal "discover" sentinel or a tuple of CIKs is valid;
    # a stray string must not be silently accepted as a universe.
    with pytest.raises(ValidationError):
        UniverseSpec(name="x", description="y", edgar_ciks="not-a-sentinel")  # type: ignore[arg-type]


class TestLoaderRejectsUnreadKeys:
    """A configured key that the loader silently ignores.

    `edgar_bulk_quarters` was added to `UniverseSpec` and written into
    `config/universe.yaml`, and nothing happened: the loader maps known keys
    one at a time and dropped it without a word. Same shape as the `.env`
    file the CLI once ignored while valid credentials sat in it — the file is
    read, the value is discarded, and no check fails.
    """

    @staticmethod
    def _write(tmp_path: Path, body: str) -> Path:
        path = tmp_path / "universe.yaml"
        path.write_text(f"universes:\n  x:\n    description: d\n    edgar_ciks: [1]\n{body}")
        return path

    def test_a_typo_is_rejected(self, tmp_path: Path) -> None:
        from treble.core.universe import UnknownUniverseKeyError

        path = self._write(tmp_path, '    edgar_bulk_quarter: ["2026q1"]\n')
        with pytest.raises(UnknownUniverseKeyError, match="edgar_bulk_quarter"):
            load_universe_config(path)

    def test_the_error_lists_what_is_readable(self, tmp_path: Path) -> None:
        """So the fix is obvious from the message alone."""
        from treble.core.universe import UnknownUniverseKeyError

        path = self._write(tmp_path, "    nonsense: 1\n")
        with pytest.raises(UnknownUniverseKeyError, match="fred_series"):
            load_universe_config(path)

    def test_every_spec_field_is_readable_from_config(self, tmp_path: Path) -> None:
        """The kill-test: add a field to UniverseSpec without wiring it into
        the loader and this fails, instead of the value vanishing.

        A type error is fine here — it proves the key was read. Only being
        told the key is unknown means the loader ignores it.
        """
        from treble.core.universe import UniverseSpec, UnknownUniverseKeyError

        for field in sorted(set(UniverseSpec.model_fields) - {"name"}):
            path = self._write(tmp_path, f"    {field}: []\n")
            try:
                load_universe_config(path)
            except UnknownUniverseKeyError as unread:
                pytest.fail(f"{field} is a UniverseSpec field the loader drops: {unread}")
            except Exception:  # noqa: S110 - a type error still proves it was read
                pass

    def test_bulk_quarters_round_trip(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, '    edgar_bulk_quarters: ["2026q1", "2025q4"]\n')
        spec = load_universe_config(path).universes["x"]
        assert spec.edgar_bulk_quarters == ("2026q1", "2025q4")


class TestEquityTickersAreConfiguration:
    """The equity universe, moved out of Python on 2026-10-03.

    It had lived in `scripts/backfill_port.py` as a module constant, which
    made `twelvedata` reachable from no universe and made the one subset of
    the security master that could only be changed by editing code — in
    contradiction of `config/universe.yaml`'s own opening line, that subsets
    are "a configuration, never a code change".
    """

    def test_the_real_config_declares_equity_tickers(self) -> None:
        config = load_universe_config(CONFIG)
        assert config.universes["dev"].equity_tickers
        assert len(config.universes["full"].equity_tickers) >= 200

    def test_every_ticker_is_a_string(self) -> None:
        """YAML 1.1 resolves six tickers to booleans.

        `ON` is ON Semiconductor and bare `ON` loads as `True`; so do OFF,
        YES, NO, TRUE and FALSE. Caught when `UniverseSpec` refused a bool,
        and the symbol would otherwise have been fetched as the string
        "True" and filed as a dead ticker. Every entry is quoted in the
        config; this is what notices if one stops being.
        """
        config = load_universe_config(CONFIG)
        for name, spec in config.universes.items():
            for ticker in spec.equity_tickers:
                assert isinstance(ticker, str), f"{name}: {ticker!r} is {type(ticker)}"
                assert ticker == ticker.strip()
                assert ticker, f"{name}: empty ticker"

    def test_the_boolean_trap_is_still_in_the_config(self) -> None:
        # Not a style assertion: it keeps a real instance of the hazard in
        # the file, so `test_every_ticker_is_a_string` is exercised against
        # the case that actually bit rather than against a hypothetical.
        assert "ON" in load_universe_config(CONFIG).universes["full"].equity_tickers

    def test_no_duplicates(self) -> None:
        # A repeated symbol costs a request from an 8-per-minute budget and
        # writes nothing, since the payload is content-addressed.
        for name, spec in load_universe_config(CONFIG).universes.items():
            assert len(spec.equity_tickers) == len(set(spec.equity_tickers)), name

    def test_each_symbol_is_its_own_step(self) -> None:
        """One step per symbol, so a half-hour run is resumable.

        A single step covering the whole list would restart all 226 to
        recover one, which is the state `treasury-auctions` was found in.
        """
        spec = load_universe_config(CONFIG).universes["dev"]
        steps = plan_steps(spec)
        equities = [s for s in steps if s.source_id == "twelvedata"]
        assert len(equities) == len(spec.equity_tickers)
        assert {s.key for s in equities} == set(spec.equity_tickers)

    def test_the_request_uri_carries_no_credential(self) -> None:
        from datetime import date

        from treble.ingest.populate import uri_for_step

        uri = uri_for_step(
            PopulationStep(source_id="twelvedata", key="ON"),
            fred_start=date(2020, 1, 1),
            fred_end=date(2026, 1, 1),
        )
        assert "symbol=ON" in uri
        assert "apikey" not in uri.lower()
