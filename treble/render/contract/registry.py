"""Screen registry — discovers `.screen.yaml` definitions (spec §7, I6).

Definitions live in `treble/screens/definitions/` as **pure data**: no
Python, no imports. That is what lets the layer contract hold — `render`
sits above `screens`, so the loader belongs here and the definitions stay
inert data that both this loader and any future renderer can consume.

"Every function is a declarative screen definition plus a resolver, which
is why the library is extensible by plugin" (§7). Most screens need no
custom resolver at all: the generic one in `resolver.py` walks the
definition and binds through TAPI. A screen that needs renderer-specific
code is a defect to escalate, not to special-case (CLAUDE.md §4).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from treble.render.contract.schema import ScreenDef, load_screen

#: Where definitions live. A directory of data files, deliberately not a
#: Python package of objects — see the module docstring.
DEFINITIONS_DIR = Path(__file__).resolve().parents[2] / "screens" / "definitions"

SCREEN_SUFFIX = ".screen.yaml"


class UnknownScreenError(KeyError):
    """No definition exists for that mnemonic."""


@lru_cache(maxsize=1)
def _load_all() -> dict[str, ScreenDef]:
    screens: dict[str, ScreenDef] = {}
    for path in sorted(DEFINITIONS_DIR.glob(f"*{SCREEN_SUFFIX}")):
        definition = load_screen(path.read_text())
        filename_mnemonic = path.name[: -len(SCREEN_SUFFIX)].upper()
        if definition.mnemonic != filename_mnemonic:
            # A mismatch means `DES <GO>` could load a screen calling itself
            # something else — caught at load time rather than in front of a
            # user.
            raise ValueError(
                f"{path.name}: declares mnemonic {definition.mnemonic!r} but the "
                f"filename says {filename_mnemonic!r}"
            )
        screens[definition.mnemonic] = definition
    return screens


def available() -> list[str]:
    """Mnemonics with a screen definition, sorted."""
    return sorted(_load_all())


def get_screen(mnemonic: str) -> ScreenDef:
    """The definition for a mnemonic.

    Raises rather than returning a blank screen: a function that silently
    renders nothing is indistinguishable from one with no data.
    """
    screens = _load_all()
    key = mnemonic.upper()
    if key not in screens:
        raise UnknownScreenError(
            f"no screen definition for {mnemonic!r}; available: {', '.join(sorted(screens))}"
        )
    return screens[key]


def has_screen(mnemonic: str) -> bool:
    return mnemonic.upper() in _load_all()
