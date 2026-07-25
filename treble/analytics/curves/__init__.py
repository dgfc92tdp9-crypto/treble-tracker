"""Curve bootstrapping, interpolation, CurveConfig content hashing (I4).

Implements specification section §11.1.
See docs/treble-tracker-spec.md and CLAUDE.md.
"""

from treble.analytics.curves.bootstrap import (
    REPRICE_TOLERANCE,
    Curve,
    CurveBuildError,
    build_curve,
)
from treble.analytics.curves.config import (
    CurveConfig,
    InstrumentKind,
    InstrumentSpec,
    Interpolation,
)
from treble.analytics.curves.hagan_west import MonotoneConvex

__all__ = [
    "REPRICE_TOLERANCE",
    "Curve",
    "CurveBuildError",
    "CurveConfig",
    "InstrumentKind",
    "InstrumentSpec",
    "Interpolation",
    "MonotoneConvex",
    "build_curve",
]
