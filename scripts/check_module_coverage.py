"""Fail if any shipped module has no coverage at all.

The repository floor is 84% and passes comfortably. It passed while
`tapi/issuer_curves.py` sat at 0% and was fitting issuer credit curves over
CLOs, MBS and auto-receivable trusts — 37% of the debt universe — and it
passed while `tapi/vol_surface.py` sat at 0% too. A floor is an average, and
an average cannot see one module at zero. That is how a real defect in
shipped output survived.

So this checks the distribution, not the mean.

**It is a gate step rather than a test, deliberately.** The first version was
a test, and it was wrong in two ways that matter here: pytest-cov writes
`.coverage` when the session *ends*, so a test reading it validates the
previous run, and on a clean checkout there is no file at all so it skipped —
a check that cannot fail, exactly when it matters most. Running after pytest
reads the data this run produced, and a missing file is an error.
"""

from __future__ import annotations

import sys
from pathlib import Path

import coverage

REPO = Path(__file__).resolve().parent.parent

#: Modules allowed to have no coverage, each with the reason. An entry is a
#: claim someone can check, not a suppression.
ALLOWED_UNCOVERED: dict[str, str] = {
    "treble/addin/udf.py": (
        "xlwings user-defined functions; importing them needs Excel, which CI does "
        "not have and the spec (§2.3) does not require. The logic they call lives "
        "in tapi and is covered there."
    ),
}


def main() -> int:
    data_file = REPO / ".coverage"
    if not data_file.exists():
        print(f"no {data_file}; run the test stage first", file=sys.stderr)
        return 1

    cov = coverage.Coverage(data_file=str(data_file))
    cov.load()
    measured: dict[str, tuple[int, int]] = {}
    for path in cov.get_data().measured_files():
        try:
            name = str(Path(path).relative_to(REPO))
        except ValueError:
            continue
        if not name.startswith("treble/"):
            continue
        _fname, statements, _excluded, missing, _fmt = cov.analysis2(path)
        measured[name] = (len(statements), len(missing))

    if not measured:
        print("coverage recorded no treble/ modules at all", file=sys.stderr)
        return 1

    failures: list[str] = []
    unmeasured = sorted(
        name
        for name, (statements, missed) in measured.items()
        if statements > 0 and missed == statements and name not in ALLOWED_UNCOVERED
    )
    if unmeasured:
        failures.append(
            "these ship with no test touching them, which the coverage floor "
            f"cannot see: {', '.join(unmeasured)}"
        )

    for name, reason in sorted(ALLOWED_UNCOVERED.items()):
        if not (REPO / name).exists():
            failures.append(f"{name} is allowlisted but does not exist")
        elif not reason.strip():
            failures.append(f"{name} is allowlisted with no reason")
        else:
            statements, missed = measured.get(name, (0, 0))
            if statements and missed < statements:
                failures.append(f"{name} now has coverage; its allowlist entry is stale")

    if failures:
        for line in failures:
            print(f"module coverage: {line}", file=sys.stderr)
        return 1
    print(f"module coverage: {len(measured)} modules, none unmeasured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
