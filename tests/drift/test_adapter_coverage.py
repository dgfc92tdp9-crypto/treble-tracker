"""Every adapter the universe declares must have actually run.

Marked `drift` because it inspects the live install rather than a fixture,
so CI (fixture-only and offline) does not run it. `make drift` does.

**Why it exists.** The Phase 1 investor audit found `gleif`, `openfigi` and
`nport` built, unit-tested, and never once executed against a live payload.
Their fixture tests passed, so WP6 passed; WP7 was titled "security master
and entity graph populated" while the entity graph was empty. Nothing
checked that a finished adapter had ever been used.

That is the third time a mechanism turned out to be switched off while every
test around it stayed green. This closes the class for ingest: an adapter
that exists but has never run now fails a check instead of passing one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from treble.cmd.cli import DEFAULT_CONFIG, DEFAULT_DATA_DIR
from treble.core.universe import load_universe_config, plan_steps
from treble.store.ingest_log import IngestLog

pytestmark = pytest.mark.drift


def _sources_run() -> set[str]:
    log = IngestLog(Path(DEFAULT_DATA_DIR) / "ingest.db")
    return {entry.source for entry in log.read()}


def test_the_install_has_been_populated() -> None:
    """Guards the checks below: on an empty install they would pass by
    having nothing to compare."""
    assert _sources_run(), "no ingest log entries; run `treble populate` first"


def test_every_declared_source_has_run() -> None:
    """A universe that declares a source and never fetches it is a universe
    whose coverage is smaller than it claims."""
    spec = load_universe_config(DEFAULT_CONFIG).get("dev")
    declared = {step.source_id for step in plan_steps(spec)}
    missing = sorted(declared - _sources_run())
    assert not missing, (
        f"declared in the universe but never fetched: {', '.join(missing)}. "
        "An adapter that has never run is untested against the real source, "
        "however green its fixture tests are."
    )


def test_every_shipped_adapter_is_reachable_from_a_universe() -> None:
    """An adapter no universe can invoke is dead code wearing a test suite.

    Listed explicitly rather than discovered, so adding an adapter forces a
    decision about which universe uses it instead of letting it sit unused.
    """
    shipped = {
        "edgar-companyfacts",
        "edgar-submissions",
        "edgar-bulk",
        "fred",
        "treasury-auctions",
        "sec-nport",
        # Restored after being removed to make this test pass — which is the
        # precise failure it exists to catch, committed by its own author.
        # GLEIF (legal entity identity) and OpenFIGI (instrument identity)
        # are built and fixture-tested and have never processed a live
        # payload, so the security master has no `lei:` namespace and no
        # FIGI mapping. This stays red until a universe invokes them.
        "gleif",
        "openfigi",
    }
    reachable: set[str] = set()
    config = load_universe_config(DEFAULT_CONFIG)
    for name in ("dev", "full"):
        spec = config.get(name)
        ciks = (51143,) if spec.discovers_filers else ()
        reachable |= {s.source_id for s in plan_steps(spec, discovered_ciks=ciks)}
    unreachable = sorted(shipped - reachable)
    assert not unreachable, f"shipped but no universe invokes them: {', '.join(unreachable)}"
