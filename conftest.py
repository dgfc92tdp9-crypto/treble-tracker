"""Pytest root conftest.

Guarantees ``treble`` is importable from a checkout regardless of how (or
whether) the environment's ``.pth`` machinery ran — some sandboxed
environments skip ``.pth`` processing, which silently breaks editable
installs. Putting the project root on ``sys.path`` here is deterministic
and costs nothing when the editable install works.
"""

import sys
from pathlib import Path

from hypothesis import settings

_ROOT = str(Path(__file__).parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# No per-example deadlines: property tests here assert mathematics, not
# timing, and both CI runners and sandboxed dev environments have I/O
# variance that makes Hypothesis's default 200ms deadline flaky.
settings.register_profile("treble", deadline=None)
settings.load_profile("treble")
