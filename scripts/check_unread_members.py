"""Fail if a computed member has no reader anywhere.

The last four defects in this repository were one shape: a value computed
and never read. The redistribution flag declared and never checked, the
placeholder set that guarded one layer and not the next, a licence list
copied instead of referenced, a model-skill gate nobody passed through.
Three of the four were written by me. **Nothing failing found any of
them** — each turned up in a manual sweep, which is not a mechanism.

So this is the mechanism. Every `@property` and annotated field under
`treble/` is checked for at least one read, across the package, the tests,
the screen definitions and the TypeScript renderer.

**The heuristic is deliberately narrow.** A first version flagged anything
with no reader *outside its defining file* and few mentions inside it,
which called `BasisSwapSpec.pay_day_count` unread when the pricer three
functions below passes it straight into the leg builder. A field used by
its own module is being used. Only a member with no read at all — not one
counting its own declaration — is reported.

Legitimate cases are named in `ALLOWED_UNREAD` with the reason, which
turns a silent hole into a line somebody has to write down and defend.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Members with no reader, each with the reason it is allowed to have none.
ALLOWED_UNREAD: dict[str, str] = {
    "CurveConfig.convexity_mean_reversion": (
        "Declared and hashed but read by no bootstrap, so two configs differing only "
        "in it hash differently and build identically. Latent — it defaults to None "
        "and nothing sets it. Left in place because removing a field changes every "
        "stored curve hash, which is a migration rather than a cleanup."
    ),
    "InputCell.input_type": (
        "Part of the serialised screen contract. Renderers read it out of the JSON "
        "payload rather than the Python object, so no source reference exists."
    ),
    "WeightedObservation.age_days": (
        "Transparency data on TVAL's contributed observations. The weighting uses a "
        "decay computed from the same dates, so nothing reads this field — but a price "
        "whose backing observations are 300 days old is a different claim from one "
        "backed by yesterday's, and §15 says the drill-down states what it rests on. "
        "Kept as a gap to close on the screen rather than deleted as dead."
    ),
    "UniverseConfig.openfigi_jobs_per_request": (
        "Documents OpenFIGI's published batch size, which the adapter enforces from "
        "its own constant. Same shape as RateLimits and tied the same way once a "
        "batching test exists; recorded rather than quietly deleted."
    ),
}


def _members(path: Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and any(
                isinstance(d, ast.Name) and d.id == "property" for d in item.decorator_list
            ):
                out.append((node.name, item.name))
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                name = item.target.id
                if not name.startswith("_") and not name.isupper():
                    out.append((node.name, name))
    return out


def main() -> int:
    src = [p for p in sorted(REPO.glob("treble/**/*.py")) if "_generated" not in str(p)]
    corpus = {p: p.read_text() for p in src}
    corpus.update({p: p.read_text() for p in REPO.glob("tests/**/*.py")})
    corpus.update({p: p.read_text() for p in REPO.glob("treble/screens/**/*.yaml")})
    corpus.update({p: p.read_text() for p in REPO.glob("treble/render/web/src/*.ts")})
    corpus.update({p: p.read_text() for p in REPO.glob("apps/desktop/src/*.ts")})

    unread: list[str] = []
    for path in src:
        own = path.read_text()
        for cls, name in _members(path):
            # Reads elsewhere, or reads in this file beyond the declaration.
            elsewhere = sum(t.count(name) for p, t in corpus.items() if p != path)
            here = len(re.findall(rf"\b{re.escape(name)}\b", own))
            declared = len(re.findall(rf"^\s*{re.escape(name)}\s*[:=]", own, re.M))
            declared += len(re.findall(rf"^\s*def {re.escape(name)}\b", own, re.M))
            if elsewhere == 0 and here - declared <= 0:
                unread.append(f"{cls}.{name}")

    failures = [m for m in unread if m not in ALLOWED_UNREAD]
    stale = [m for m in ALLOWED_UNREAD if m not in unread]

    if failures:
        print(
            "unread members: these are computed and nothing reads them, which is the "
            f"shape of the last four defects here: {', '.join(sorted(failures))}",
            file=sys.stderr,
        )
        print("  add a reader, delete it, or name it in ALLOWED_UNREAD with why.", file=sys.stderr)
    for m in stale:
        print(
            f"unread members: {m} now has a reader; its allowlist entry is stale", file=sys.stderr
        )
    if failures or stale:
        return 1
    print(f"unread members: none ({len(ALLOWED_UNREAD)} allowlisted with reasons)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
