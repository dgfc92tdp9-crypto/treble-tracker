"""Declared rate limits must match what the adapters enforce (spec §9.1).

`RateLimits` in `core/universe.py` had no reader: every adapter builds its
token bucket from its own `SourceMeta.rate_limit_per_second`. The values
agree today, which is why nobody noticed, and is precisely the state that
breaks silently the first time somebody lowers a limit in the config to be
polite to a source and gets the adapter's hardcoded value instead.

`core` cannot import `ingest` to derive them — core is the bottom of the
layering contract — so this is the seam that holds them together. A test
can import both.
"""

from __future__ import annotations

import pytest

from treble.core.universe import RateLimits
from treble.ingest.registry import all_sources


@pytest.mark.parametrize(
    ("field", "source_id", "scale"),
    [
        ("edgar_per_second", "edgar-companyfacts", 1.0),
        ("gleif_per_second", "gleif", 1.0),
        ("treasury_per_second", "treasury-auctions", 1.0),
        ("openfigi_per_minute", "openfigi", 60.0),
    ],
)
def test_the_config_matches_the_adapter(field: str, source_id: str, scale: float) -> None:
    """The adapter is authoritative -- it is where the source's published
    limit was read. This fails when the config drifts from it."""
    sources = all_sources()
    if source_id not in sources:
        pytest.fail(
            f"{source_id} is named in RateLimits but no adapter declares it; the config "
            "documents a limit for a source that does not exist"
        )
    declared = getattr(RateLimits(), field)
    enforced = sources[source_id].rate_limit_per_second
    assert enforced is not None, f"{source_id} declares no rate limit at all"
    assert enforced * scale == pytest.approx(declared), (
        f"{field}={declared} but {source_id} enforces {enforced}/s. The adapter wins; "
        "update the config, or the number in it is decoration"
    )
