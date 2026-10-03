"""The vendor coverage check, and proof it can fail in both directions.

A check that only ever passes is the failure this repository keeps
cataloguing. `treble coverage` reported "221 of 221 served" the first time
it ran, which is the correct answer and also exactly what a check with a
broken comparison would print. So the tests below drive `compare` to a
non-empty `missing` and a non-empty `returned`, and assert the empty
catalogue is announced rather than reported as universal failure.

No network: `catalogue()` is the only function that touches it, and it is
split from `symbols_in()` precisely so the parsing and the comparison are
testable against bytes.
"""

from __future__ import annotations

import json

import pytest

from treble.core.universe import UniverseSpec
from treble.ingest.coverage import compare, symbols_in


def _spec(tickers: tuple[str, ...], unavailable: dict[str, str] | None = None) -> UniverseSpec:
    return UniverseSpec(
        name="t",
        description="d",
        edgar_ciks=(),
        equity_tickers=tickers,
        equity_tickers_unavailable=unavailable or {},
    )


def _payload(symbols: list[str]) -> bytes:
    # The vendor's real envelope: {"data": [{"symbol": ..., ...}], "status": "ok"}
    return json.dumps(
        {
            "data": [
                {"symbol": s, "name": f"{s} Inc", "currency": "USD", "exchange": "NASDAQ"}
                for s in symbols
            ],
            "status": "ok",
        }
    ).encode()


class TestSymbolsIn:
    def test_the_real_envelope_parses(self) -> None:
        assert symbols_in(_payload(["AAPL", "MSFT"])) == {"AAPL", "MSFT"}

    def test_a_bare_list_also_parses(self) -> None:
        # Some reference endpoints return the list unwrapped. Accepted
        # rather than refused, since the alternative is a hard failure on a
        # response that plainly contains the answer.
        body = json.dumps([{"symbol": "AAPL"}]).encode()
        assert symbols_in(body) == {"AAPL"}

    def test_rows_without_a_symbol_are_skipped(self) -> None:
        body = json.dumps({"data": [{"symbol": "AAPL"}, {"name": "no symbol"}, {}]}).encode()
        assert symbols_in(body) == {"AAPL"}

    def test_a_vendor_error_is_raised_not_parsed_as_empty(self) -> None:
        """An error envelope must not read as "the vendor serves nothing".

        Returning an empty set here would make every configured symbol
        `missing`, and the report would recommend deleting a working
        universe.
        """
        body = json.dumps({"status": "error", "message": "invalid api key"}).encode()
        with pytest.raises(RuntimeError, match="invalid api key"):
            symbols_in(body)

    def test_an_unrecognised_envelope_is_raised(self) -> None:
        body = json.dumps({"results": [{"symbol": "AAPL"}]}).encode()
        with pytest.raises(RuntimeError, match="no instrument list"):
            symbols_in(body)
        # ...and the error names what it did find, so the fix is visible.
        with pytest.raises(RuntimeError, match="results"):
            symbols_in(body)


class TestTheCheckCanFail:
    def test_a_configured_symbol_the_vendor_lacks_is_reported(self) -> None:
        # The DFS/HES/EA case: written into the config from recollection,
        # delisted in reality.
        report = compare(_spec(("AAPL", "DFS")), symbols_in(_payload(["AAPL"])))
        assert report.missing == ("DFS",)
        assert report.served == ("AAPL",)
        assert not report.ok
        assert any("not served: DFS" in line for line in report.lines())

    def test_a_stale_exclusion_is_reported(self) -> None:
        """The direction a comment could never check.

        AVB and EQR are excluded because this tier does not serve them. If
        that changes, nothing would notice — the note saying why is not
        reread. This is what notices.
        """
        report = compare(
            _spec(("AAPL",), {"AVB": "not served"}),
            symbols_in(_payload(["AAPL", "AVB"])),
        )
        assert report.returned == ("AVB",)
        assert not report.ok
        assert any("now served: AVB" in line for line in report.lines())

    def test_both_directions_at_once(self) -> None:
        report = compare(
            _spec(("AAPL", "GONE"), {"BACK": "was not served"}),
            symbols_in(_payload(["AAPL", "BACK"])),
        )
        assert report.missing == ("GONE",)
        assert report.returned == ("BACK",)

    def test_the_passing_case_is_genuinely_passing(self) -> None:
        # The positive half. Without it the tests above would pass against a
        # `compare` that marked everything missing.
        report = compare(_spec(("AAPL", "MSFT")), symbols_in(_payload(["AAPL", "MSFT", "X"])))
        assert report.ok
        assert report.missing == ()
        assert report.returned == ()
        assert report.served == ("AAPL", "MSFT")


class TestAnEmptyCatalogueIsLoud:
    def test_it_is_announced_rather_than_read_as_universal_failure(self) -> None:
        """Every symbol missing and a broken filter render identically.

        `catalogue_size` exists to separate them, because the recommended
        action is opposite: fix the universe, or fix the request.
        """
        report = compare(_spec(("AAPL", "MSFT")), frozenset())
        assert report.catalogue_size == 0
        assert len(report.missing) == 2
        assert any("EMPTY" in line for line in report.lines())
        assert any("unproven" in line for line in report.lines())

    def test_a_populated_catalogue_does_not_warn(self) -> None:
        report = compare(_spec(("AAPL",)), symbols_in(_payload(["AAPL", "MSFT"])))
        assert report.catalogue_size == 2
        assert not any("EMPTY" in line for line in report.lines())


class TestAgainstTheRealConfig:
    def test_no_symbol_is_both_configured_and_unavailable(self) -> None:
        from pathlib import Path

        from treble.core.universe import load_universe_config

        root = Path(__file__).resolve().parents[2]
        for name, spec in load_universe_config(root / "config" / "universe.yaml").universes.items():
            overlap = set(spec.equity_tickers) & set(spec.equity_tickers_unavailable)
            assert not overlap, f"{name}: {sorted(overlap)} is both tracked and excluded"

    def test_every_exclusion_states_a_reason(self) -> None:
        from pathlib import Path

        from treble.core.universe import load_universe_config

        root = Path(__file__).resolve().parents[2]
        config = load_universe_config(root / "config" / "universe.yaml")
        for name, spec in config.universes.items():
            for symbol, reason in spec.equity_tickers_unavailable.items():
                # A bare exclusion is the comment problem again: the symbol
                # is gone and nobody can tell whether that was deliberate.
                assert len(reason) > 15, f"{name}: {symbol} has no usable reason"
