"""Bootstrap repricing property (CLAUDE.md §7): the curve must reprice every
input instrument to within 1e-10, as a property, on every curve, always —
across all supported interpolation methods.

Quotes here are synthetic test fixtures (clearly marked; no fabricated
market data presented as real).
"""

from datetime import date

import pytest

from treble.analytics.curves import (
    Curve,
    CurveBuildError,
    CurveConfig,
    InstrumentKind,
    InstrumentSpec,
    Interpolation,
    build_curve,
)
from treble.analytics.curves.bootstrap import _instrument_times, _residuals

AS_OF = date(2026, 7, 24)

# Synthetic but realistically shaped USD OIS-style quote set (test fixture).
QUOTES: dict[tuple[InstrumentKind, str], float] = {
    (InstrumentKind.DEPOSIT, "1W"): 0.0435,
    (InstrumentKind.DEPOSIT, "3M"): 0.0432,
    (InstrumentKind.OIS, "1Y"): 0.0410,
    (InstrumentKind.OIS, "2Y"): 0.0388,
    (InstrumentKind.OIS, "3Y"): 0.0375,
    (InstrumentKind.OIS, "5Y"): 0.0368,
    (InstrumentKind.OIS, "7Y"): 0.0371,
    (InstrumentKind.OIS, "10Y"): 0.0380,
    (InstrumentKind.OIS, "30Y"): 0.0392,
}

INSTRUMENTS = tuple(InstrumentSpec(kind=kind, tenor=tenor) for kind, tenor in QUOTES)


def make_config(method: Interpolation) -> CurveConfig:
    return CurveConfig(
        name=f"TEST-USD-{method.value}",
        currency="USD",
        instruments=INSTRUMENTS,
        interpolation=method,
    )


@pytest.mark.golden
@pytest.mark.parametrize("method", list(Interpolation))
class TestRepricingProperty:
    def test_builds_and_reprices_all_inputs_to_1e10(self, method: Interpolation) -> None:
        config = make_config(method)
        # build_curve itself enforces the tolerance; a returned curve IS the
        # assertion. Verify residuals independently anyway.
        curve = build_curve(config, QUOTES, as_of=AS_OF)
        import numpy as np

        instruments = []
        from treble.analytics.curves.bootstrap import _PricedInstrument

        for spec in config.instruments:
            t_mat, pay_times, accruals, dep = _instrument_times(config, spec, AS_OF)
            instruments.append(
                _PricedInstrument(
                    spec=spec,
                    quote=QUOTES[(spec.kind, spec.tenor)],
                    maturity_time=t_mat,
                    pay_times=pay_times,
                    accruals=accruals,
                    deposit_accrual=dep,
                )
            )
        instruments.sort(key=lambda i: i.maturity_time)
        import QuantLib as ql

        from treble.analytics import _ql

        cal = _ql.calendar(config.calendar)
        dc = _ql.day_counter(config.day_count)
        start = _ql.to_ql_date(AS_OF)
        spot = cal.advance(start, ql.Period(config.settlement_days, ql.Days))
        t_spot = dc.yearFraction(start, spot)
        residuals = _residuals(
            np.array(curve.node_zeros), config, curve.node_times, instruments, t_spot
        )
        assert float(np.max(np.abs(residuals))) <= 1e-10

    def test_deterministic(self, method: Interpolation) -> None:
        config = make_config(method)
        a = build_curve(config, QUOTES, as_of=AS_OF)
        b = build_curve(config, QUOTES, as_of=AS_OF)
        assert a.node_zeros == b.node_zeros


class TestCurveIdentity:
    def test_curve_requires_config(self) -> None:
        with pytest.raises(TypeError, match="I4"):
            Curve("not-a-config", AS_OF, (1.0,), (0.04,))  # type: ignore[arg-type]

    def test_curve_exposes_config_hash(self) -> None:
        config = make_config(Interpolation.MONOTONE_CONVEX)
        curve = build_curve(config, QUOTES, as_of=AS_OF)
        assert curve.content_hash == config.content_hash

    def test_missing_quote_is_an_error_not_a_skip(self) -> None:
        config = make_config(Interpolation.MONOTONE_CONVEX)
        partial = dict(QUOTES)
        del partial[(InstrumentKind.OIS, "30Y")]
        with pytest.raises(CurveBuildError, match="no quote"):
            build_curve(config, partial, as_of=AS_OF)

    def test_discounts_decreasing_under_positive_rates(self) -> None:
        curve = build_curve(make_config(Interpolation.MONOTONE_CONVEX), QUOTES, as_of=AS_OF)
        from itertools import pairwise

        ts = [30.0 * i / 300.0 + 0.05 for i in range(300)]
        assert all(curve.discount(a) >= curve.discount(b) for a, b in pairwise(ts))
