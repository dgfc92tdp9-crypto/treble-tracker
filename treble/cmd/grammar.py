"""The command grammar (spec §5).

    [SECURITY] [<YELLOW KEY>] [FUNCTION] [<GO>]

Fluency in this small formal language is what separates a novice from an
expert user (§5), so the parser has to be exact about the rules rather than
approximately right:

- **`<GO>` is the execute token.** Nothing happens until it is pressed; the
  command line is editable state until submitted (§5.2). The parser
  therefore reports whether a line is *complete* or still being typed, and
  a caller must not dispatch an incomplete line.
- **The yellow key selects the asset-class namespace** (§3.1). `IBM` alone
  is ambiguous — equity, bond, CDS or option — so a security reference
  without a namespace is not silently assumed to be an equity.
- **Function alone is global** (`WEI <GO>`); **security alone** opens that
  security's menu (§5.2).
- **Chained arguments** follow the function: `HP IBM US Equity 1/1/24
  12/31/24 <GO>` (§5.2).
- **Anything unresolvable routes to `ASK`** (§5.2, §20.2) rather than
  erroring — the user never hits a dead end. `ASK` itself is Phase 5, so
  this parser *classifies* the input as ASK-bound; it does not answer it.

Mnemonics are validated against the glossary (§24). An unknown mnemonic is
not a syntax error — it is exactly the case that routes to ASK.
"""

from __future__ import annotations

import enum

from lark import Lark, Token
from lark.exceptions import LarkError
from pydantic import BaseModel, ConfigDict

from treble.core.identifiers import SecurityQuery, YellowKey, parse_security

#: The execute token. Rendered as <GO> in the spec; typed as GO.
GO = "GO"

#: Mnemonics from spec §24. This is the contract — CLAUDE.md forbids
#: coining new ones, so anything absent here routes to ASK rather than
#: being invented into existence.
KNOWN_MNEMONICS: frozenset[str] = frozenset(
    [
        "HELP",
        "MENU",
        "TPS",
        "FCTN",
        "FLDS",
        "CNVS",
        "DRQ",
        "TU",
        "SPTR",
        "MDL",
        "PLUG",
        "DES",
        "CN",
        "FA",
        "FAM",
        "ERN",
        "EE",
        "EEO",
        "EEB",
        "ANR",
        "DVD",
        "CACS",
        "HDS",
        "OWN",
        "RELS",
        "SPLC",
        "CAST",
        "DDIS",
        "CRPR",
        "DRSK",
        "ESG",
        "CO2",
        "DOCS",
        "TRAN",
        "WACC",
        "EQRV",
        "TI",
        "Q",
        "QM",
        "QR",
        "GIP",
        "TAQ",
        "ALLQ",
        "TCMP",
        "TGN",
        "TDH",
        "MOST",
        "IMAP",
        "WEI",
        "BTMM",
        "WCDS",
        "FXIP",
        "TOP",
        "NI",
        "WIRE",
        "MBTR",
        "MBWD",
        "DEPO",
        "GC",
        "GP",
        "HP",
        "HS",
        "COMP",
        "G",
        "TECH",
        "STDY",
        "BT",
        "SPRD",
        "EQS",
        "SRCH",
        "FSRC",
        "SECF",
        "NIM",
        "LEAG",
        "MA",
        "PGM",
        "PSCR",
        "ECO",
        "ECST",
        "ECFC",
        "ECWB",
        "WIRP",
        "FOMC",
        "EMOD",
        "TRADE",
        "YAS",
        "YA",
        "OAS1",
        "CSHF",
        "HZ",
        "HR",
        "FIW",
        "TVAL",
        "CDSW",
        "CRVD",
        "YT",
        "MTCS",
        "MTSP",
        "CLC",
        "ASW",
        "ICVS",
        "SWDF",
        "CRVF",
        "FWCM",
        "VCUB",
        "OVDV",
        "SKEW",
        "HVG",
        "SWPM",
        "DLIB",
        "OMON",
        "OSA",
        "OVME",
        "OVML",
        "RISK",
        "VCON",
        "FRD",
        "PRTU",
        "PORT",
        "PMEN",
        "SCEN",
        "TIDX",
        "EMS",
        "PMS",
        "DESK",
        "FXT",
        "BOLT",
        "RFQ",
        "TCA",
        "BSKT",
        "IM",
        "MSG",
        "PEOP",
        "NOTE",
        "ALRT",
        "CALN",
        "EVTS",
        "RES",
        "TQNT",
        "TQL",
        "API",
        "ASK",
    ]
)

_GRAMMAR = r"""
    ?line: token*
    token: WORD
    WORD: /[^\s]+/
    %import common.WS
    %ignore WS
"""

_parser = Lark(_GRAMMAR, start="line")


class CommandKind(enum.Enum):
    """What the parsed line asks for (§5.2)."""

    FUNCTION_ON_SECURITY = "function_on_security"  # IBM US Equity DES
    GLOBAL_FUNCTION = "global_function"  # WEI
    SECURITY_MENU = "security_menu"  # IBM US Equity
    MENU_NUMBER = "menu_number"  # 3
    ASK = "ask"  # anything unresolvable (§20.2)
    EMPTY = "empty"


class ParsedCommand(BaseModel):
    """The result of parsing one command line."""

    model_config = ConfigDict(frozen=True)

    kind: CommandKind
    raw: str
    #: True only when the line ended with the execute token (§5.2).
    complete: bool
    security: SecurityQuery | None = None
    function: str | None = None
    arguments: tuple[str, ...] = ()
    menu_number: int | None = None
    #: Why the line routed to ASK, for the "here is the faster mnemonic"
    #: teaching behaviour (§20.2).
    ask_reason: str | None = None

    @property
    def dispatchable(self) -> bool:
        """A caller must not act on an incomplete line: `<GO>` is a
        deliberate commit step (§5.2)."""
        return self.complete and self.kind is not CommandKind.EMPTY


def _split_go(tokens: list[str]) -> tuple[list[str], bool]:
    if tokens and tokens[-1].upper() == GO:
        return tokens[:-1], True
    return tokens, False


def _find_yellow_key(tokens: list[str]) -> int | None:
    for index, token in enumerate(tokens):
        try:
            YellowKey.parse(token)
        except ValueError:
            continue
        return index
    return None


def parse_command(line: str) -> ParsedCommand:
    """Parse a command line into a dispatchable intent.

    Never raises on malformed input: unresolvable lines route to ASK, which
    is the spec's guarantee that a user never hits a dead end (§5.2).
    """
    raw = line.strip()
    if not raw:
        return ParsedCommand(kind=CommandKind.EMPTY, raw=line, complete=False)

    try:
        tree = _parser.parse(raw)
        tokens = [str(t) for t in tree.scan_values(lambda v: isinstance(v, Token))]
    except LarkError:
        # A character the grammar cannot tokenise (control characters, odd
        # unicode) must not be a dead end: §5.2 guarantees every input goes
        # somewhere, so it falls through to whitespace splitting and, from
        # there, to ASK. Found by the fuzz corpus on a vertical tab.
        tokens = raw.split()
    tokens = tokens or raw.split()
    body, complete = _split_go(tokens)

    if not body:
        # Bare <GO> submits an empty line.
        return ParsedCommand(kind=CommandKind.EMPTY, raw=line, complete=complete)

    # Numbered menu selection (§5.2): a number alone picks that option.
    # isdecimal, not isdigit: isdigit() is True for superscripts like "¹"
    # which int() then rejects. Found by the fuzz corpus.
    if len(body) == 1 and body[0].isdecimal():
        return ParsedCommand(
            kind=CommandKind.MENU_NUMBER,
            raw=line,
            complete=complete,
            menu_number=int(body[0]),
        )

    key_index = _find_yellow_key(body)

    if key_index is None:
        # No namespace. A single known mnemonic is a global function.
        head = body[0].upper()
        if head in KNOWN_MNEMONICS:
            return ParsedCommand(
                kind=CommandKind.GLOBAL_FUNCTION,
                raw=line,
                complete=complete,
                function=head,
                arguments=tuple(body[1:]),
            )
        return ParsedCommand(
            kind=CommandKind.ASK,
            raw=line,
            complete=complete,
            ask_reason=(
                f"{body[0]!r} is not a known function, and no asset-class key "
                "was given to resolve it as a security"
            ),
        )

    # A yellow key is present: everything up to and including it is the
    # security reference; what follows is the function and its arguments.
    try:
        security = parse_security(" ".join(body[: key_index + 1]))
    except ValueError as exc:
        return ParsedCommand(kind=CommandKind.ASK, raw=line, complete=complete, ask_reason=str(exc))

    tail = body[key_index + 1 :]
    if not tail:
        return ParsedCommand(
            kind=CommandKind.SECURITY_MENU,
            raw=line,
            complete=complete,
            security=security,
        )

    function = tail[0].upper()
    if function not in KNOWN_MNEMONICS:
        return ParsedCommand(
            kind=CommandKind.ASK,
            raw=line,
            complete=complete,
            security=security,
            ask_reason=f"{tail[0]!r} is not a mnemonic in the glossary (spec §24)",
        )
    return ParsedCommand(
        kind=CommandKind.FUNCTION_ON_SECURITY,
        raw=line,
        complete=complete,
        security=security,
        function=function,
        arguments=tuple(tail[1:]),
    )
