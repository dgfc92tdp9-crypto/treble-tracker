"""Every Makefile target must be declared `.PHONY`.

This exists because one was not, and the consequence was invisible for as
long as nobody looked at a clean checkout.

`make check` depends on `proto`, which runs `scripts/gen_proto.sh` to
generate the gRPC stubs. The stubs are gitignored, and the comment in that
script says they are "regenerated on every `make check`". They were not.
There is a *directory* called `proto/`, so make saw the target name as an
up-to-date file and skipped the recipe every single time. `make proto`
printed "`proto' is up to date." and did nothing.

Locally this was undetectable: the directory existed because the script had
been run by hand once, so every test passed. On a clean checkout — which is
what CI is — the stubs were never generated and every gRPC test failed at
import with `ModuleNotFoundError`.

That is failure class A and D at once: a mechanism whose existence was
verified and whose effect was not, resting on an environment assumption
(the generated directory happens to be present) that CI did not share. The
fix is one line in the Makefile; this is what stops the next one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"


def declared_phony() -> set[str]:
    names: set[str] = set()
    for line in MAKEFILE.read_text().splitlines():
        if line.startswith(".PHONY:"):
            names.update(line.removeprefix(".PHONY:").split())
    return names


def defined_targets() -> set[str]:
    """Target names, excluding variable assignments and pattern rules."""
    return set(re.findall(r"^([a-zA-Z][\w-]*):(?!=)", MAKEFILE.read_text(), re.MULTILINE))


def test_every_target_is_declared_phony() -> None:
    """None of these targets builds a file of its own name, so make must be
    told not to look for one. `proto` looked for the `proto/` directory,
    found it, and skipped the recipe."""
    missing = sorted(defined_targets() - declared_phony())
    assert not missing, (
        "Makefile targets not declared .PHONY: "
        + ", ".join(missing)
        + ". If any collides with a file or directory name, make will silently "
        "skip its recipe and every check that depends on it."
    )


def test_no_phony_declaration_is_stale() -> None:
    """The reverse: a `.PHONY` name with no target is a rename nobody
    finished, and it reads as though the target is still protected."""
    stray = sorted(declared_phony() - defined_targets())
    assert not stray, f".PHONY names no such target: {', '.join(stray)}"


@pytest.mark.parametrize("target", ["proto", "check"])
def test_the_targets_that_generate_code_exist(target: str) -> None:
    """`check` depends on `proto`; if either is renamed, the guarantee that a
    clean checkout regenerates the stubs goes with it."""
    assert target in defined_targets()


def test_check_still_depends_on_proto() -> None:
    """The dependency is the whole guarantee. Without it a clean checkout has
    no generated stubs and the gRPC suite fails at import — which is how this
    was found."""
    text = MAKEFILE.read_text()
    match = re.search(r"^check:([^\n#]*)", text, re.MULTILINE)
    assert match is not None, "no `check` target"
    assert "proto" in match.group(1).split(), (
        "`make check` no longer depends on `proto`, so a clean checkout would "
        "run the suite against whatever stubs happened to be lying around"
    )
