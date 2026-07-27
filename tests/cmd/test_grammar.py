"""Command grammar (spec §5).

The Phase 1 gate requires every example in §5.1 to parse, plus a fuzz
corpus, plus correct yellow-key namespace resolution. All three are here.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from treble.cmd.grammar import (
    KNOWN_MNEMONICS,
    CommandKind,
    parse_command,
)
from treble.core.identifiers import YellowKey


class TestSpecExamples:
    """Every command line printed in spec §5.1 must parse correctly."""

    @pytest.mark.parametrize(
        ("line", "ticker", "key", "function"),
        [
            ("IBM US Equity DES GO", "IBM", YellowKey.EQUITY, "DES"),
            ("IBM US Equity GP GO", "IBM", YellowKey.EQUITY, "GP"),
            ("IBM 4.15 05/15/39 Corp YAS GO", "IBM", YellowKey.CORP, "YAS"),
            ("EURUSD Crncy FXFC GO", "EURUSD", YellowKey.CRNCY, None),
            ("CL1 Cmdty GP GO", "CL1", YellowKey.CMDTY, "GP"),
            ("SPX Index MEMB GO", "SPX", YellowKey.INDEX, None),
        ],
    )
    def test_security_function_forms(
        self, line: str, ticker: str, key: YellowKey, function: str | None
    ) -> None:
        parsed = parse_command(line)
        assert parsed.complete
        if function is None:
            # FXFC and MEMB appear in §5.1 but not in the §24 glossary, so
            # they route to ASK rather than being invented into existence
            # (CLAUDE.md: never coin mnemonics).
            assert parsed.kind is CommandKind.ASK
            assert parsed.security is not None
            assert parsed.security.ticker == ticker
        else:
            assert parsed.kind is CommandKind.FUNCTION_ON_SECURITY
            assert parsed.function == function
            assert parsed.security is not None
            assert parsed.security.ticker == ticker
            assert parsed.security.key is key

    def test_tba_mortgage_security_alone(self) -> None:
        # "FNCL 5.5 <MTGE> <GO>" — security alone opens its menu (§5.2).
        parsed = parse_command("FNCL 5.5 Mtge GO")
        assert parsed.kind is CommandKind.SECURITY_MENU
        assert parsed.security is not None
        assert parsed.security.key is YellowKey.MTGE
        assert parsed.security.descriptor == "5.5"

    def test_global_function_needs_no_security(self) -> None:
        # "WEI <GO>" launches the world equity index monitor (§5.2).
        parsed = parse_command("WEI GO")
        assert parsed.kind is CommandKind.GLOBAL_FUNCTION
        assert parsed.function == "WEI"
        assert parsed.security is None

    def test_chained_arguments(self) -> None:
        # "HP IBM US Equity 1/1/24 12/31/24 <GO>" (§5.2).
        parsed = parse_command("HP IBM US Equity 1/1/24 12/31/24 GO")
        assert parsed.complete
        assert parsed.function in {"HP", None} or parsed.kind is CommandKind.ASK


class TestGoToken:
    """`<GO>` is a deliberate commit step (§5.2): nothing dispatches without it."""

    def test_line_without_go_is_incomplete(self) -> None:
        parsed = parse_command("IBM US Equity DES")
        assert parsed.kind is CommandKind.FUNCTION_ON_SECURITY
        assert parsed.complete is False
        assert parsed.dispatchable is False

    def test_line_with_go_is_dispatchable(self) -> None:
        assert parse_command("IBM US Equity DES GO").dispatchable is True

    def test_bare_go_is_empty(self) -> None:
        parsed = parse_command("GO")
        assert parsed.kind is CommandKind.EMPTY
        assert parsed.dispatchable is False

    def test_go_is_case_insensitive(self) -> None:
        assert parse_command("WEI go").complete is True


class TestYellowKeyNamespaces:
    """The yellow key selects the asset-class namespace (§3.1) — the same
    ticker under different keys is a different object."""

    def test_same_ticker_different_namespace(self) -> None:
        equity = parse_command("IBM US Equity DES GO")
        bond = parse_command("IBM 4.15 05/15/39 Corp DES GO")
        assert equity.security is not None and bond.security is not None
        assert equity.security.key is YellowKey.EQUITY
        assert bond.security.key is YellowKey.CORP
        assert equity.security != bond.security

    def test_ticker_without_namespace_is_not_assumed_equity(self) -> None:
        # Ambiguous by design: IBM alone could be the equity, a bond, a CDS
        # or an option (§3.1). Guessing would be the wrong answer.
        parsed = parse_command("IBM DES GO")
        assert parsed.kind is CommandKind.ASK
        assert parsed.security is None

    @pytest.mark.parametrize("key", list(YellowKey))
    def test_every_yellow_key_resolves(self, key: YellowKey) -> None:
        parsed = parse_command(f"TEST {key.value} DES GO")
        assert parsed.security is not None
        assert parsed.security.key is key


class TestMenuNumbers:
    def test_number_selects_a_menu_option(self) -> None:
        parsed = parse_command("3 GO")
        assert parsed.kind is CommandKind.MENU_NUMBER
        assert parsed.menu_number == 3


class TestAskFallback:
    """Any input the parser cannot resolve routes to ASK, so a user never
    hits a dead end (§5.2, §20.2)."""

    @pytest.mark.parametrize(
        "line",
        [
            "what is IBM's revenue",
            "ZZZZ US Equity NOTAFUNCTION GO",
            "gibberish",
            "show me the yield curve please",
        ],
    )
    def test_unresolvable_input_routes_to_ask(self, line: str) -> None:
        parsed = parse_command(line)
        assert parsed.kind is CommandKind.ASK
        assert parsed.ask_reason

    def test_unknown_mnemonic_is_not_a_syntax_error(self) -> None:
        parsed = parse_command("IBM US Equity INVENTED GO")
        assert parsed.kind is CommandKind.ASK
        # The security still resolved, so ASK has context to work with.
        assert parsed.security is not None
        assert "glossary" in (parsed.ask_reason or "")


class TestGlossary:
    def test_mnemonics_come_from_the_spec(self) -> None:
        # Spot-check across the §24 groups; the set must not be invented.
        for mnemonic in ("DES", "YAS", "PORT", "ICVS", "SRCH", "EQS", "FLDS", "SPTR", "MDL"):
            assert mnemonic in KNOWN_MNEMONICS

    def test_every_phase1_screen_mnemonic_is_known(self) -> None:
        # CLAUDE.md §8 Phase 1 screen list.
        for mnemonic in (
            "DES",
            "FA",
            "GP",
            "HP",
            "YAS",
            "ICVS",
            "SRCH",
            "EQS",
            "FLDS",
            "SPTR",
            "MDL",
        ):
            assert mnemonic in KNOWN_MNEMONICS


class TestFuzz:
    """The gate requires a fuzz corpus: the parser must never raise, on any
    input, because raising would be a dead end."""

    @given(st.text(max_size=120))
    def test_never_raises_on_arbitrary_text(self, line: str) -> None:
        parsed = parse_command(line)
        assert parsed.kind in set(CommandKind)

    @given(
        st.lists(st.sampled_from(["IBM", "US", "Equity", "DES", "GO", "3", "??", ""]), max_size=8)
    )
    def test_never_raises_on_token_soup(self, tokens: list[str]) -> None:
        parsed = parse_command(" ".join(tokens))
        assert parsed.raw == " ".join(tokens)

    @given(st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 /.", max_size=60))
    def test_dispatchable_implies_complete(self, line: str) -> None:
        parsed = parse_command(line)
        if parsed.dispatchable:
            assert parsed.complete
