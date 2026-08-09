"""A populated store, so the binding layer can be tested.

`tapi/local.py` is the least-covered module in the repository — 60%, 190
statements missed — and the reason is uniform across every thin method:
each needs a store holding curves, or holdings, or fundamentals, and
building that by hand is most of the work of the test. Eight methods each
paid that cost separately, so most of them did not pay it at all.

This is the shared cost. Each `with_*` call adds one kind of evidence and
returns the builder, so a test asks for exactly what its method reads and
nothing else — a test that quietly depended on data it never requested
would pass for a reason its author did not choose.

**Everything is knowable before `KNOWN`.** Facts carry a `knowledge_from`
of noon and the tests query later. That is not a detail: a store written
at noon and queried at midnight returns nothing, correctly, under I2 — and
getting it wrong cost an afternoon once already, because an empty result
looks exactly like a broken query.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from treble.core.facts import Fact
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.swap_market import DISCOUNT_CURVE, FORECAST_CURVE, USD_DISCOUNT_CURVE

#: When every fact becomes known. Tests query after it.
KNOWN = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
#: A safe `as_of` for any test using this builder.
LATER = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)
#: The report date every curve and holding describes.
DAY = date(2026, 7, 31)

_EUR_RATES = {
    "1Y": 0.0295,
    "2Y": 0.0301,
    "3Y": 0.0303,
    "5Y": 0.0305,
    "7Y": 0.0311,
    "10Y": 0.0322,
    "20Y": 0.0338,
    "30Y": 0.0330,
}
_USD_RATES = {t: r + 0.0135 for t, r in _EUR_RATES.items()}


class StoreBuilder:
    """Builds a store one kind of evidence at a time."""

    def __init__(self, path: object) -> None:
        self.store = DuckStore(path)  # type: ignore[arg-type]
        self._n = 0

    def _write(self, source: str, rows: list[tuple[str, str, object, date]]) -> StoreBuilder:
        self._n += 1
        prov = Provenance(
            source_system=source,
            source_uri=f"https://example.invalid/{source}/{self._n}",
            retrieved_at=KNOWN,
            method=ExtractionMethod.BULK_FILE,
            extractor_version="1",
            payload_hash=f"{self._n:064d}",
        )
        self.store.write_provenance([prov])
        self.store.write_facts(
            [
                Fact(
                    subject=subject,
                    field=field,
                    value=value,
                    effective_from=when,
                    effective_to=when,
                    knowledge_from=KNOWN,
                    provenance_id=prov.id,
                )
                for subject, field, value, when in rows
            ]
        )
        return self

    def with_curves(self, *, usd: bool = False) -> StoreBuilder:
        """EUR discount and forecast curves, optionally the USD leg too."""
        rows = [
            (f"swap:{curve}:{tenor}", "PAR_RATE", rate, DAY)
            for curve in (DISCOUNT_CURVE, FORECAST_CURVE)
            for tenor, rate in _EUR_RATES.items()
        ]
        if usd:
            rows += [
                (f"swap:{USD_DISCOUNT_CURVE}:{tenor}", "PAR_RATE", rate, DAY)
                for tenor, rate in _USD_RATES.items()
            ]
        return self._write("dtcc-sdr", rows)

    def with_bonds(self, count: int = 60, *, issuers: int = 4) -> StoreBuilder:
        """Corporate bonds an issuer curve can be fitted through.

        Priced off a smooth spread so the curve fits and the residuals are
        small but not zero — a set that fitted perfectly would make every
        rich/cheap call zero and hide whether the residual layer ran at all.
        """
        rows: list[tuple[str, str, object, date]] = []
        for i in range(count):
            issuer = i % issuers
            years = 2 + (i % 12)
            coupon = 0.035 + 0.001 * issuer
            # A price near par, drifting with maturity and jittered per bond.
            price = 99.0 + 0.05 * years + ((i * 37) % 11 - 5) * 0.08
            par = 1_000_000.0
            for field, value in (
                ("nport:lei", f"LEI{issuer:017d}"),
                ("nport:assetCat", "DBT"),
                ("nport:issuerCat", "CORP"),
                ("nport:name", f"Issuer {issuer}"),
                ("nport:curCd", "USD"),
                ("nport:maturityDt", date(DAY.year + years, 6, 30)),
                ("nport:annualizedRt", coupon),
                ("nport:balance", par),
                ("nport:valUSD", par * price / 100.0),
            ):
                rows.append((f"isin:US{i:010d}", field, value, DAY))
        return self._write("edgar-nport", rows)

    def with_factors(self, days: int = 250, assets: int = 5) -> StoreBuilder:
        """Ken French factor returns and asset returns that load on them.

        Two things here were wrong on the first attempt and both produced a
        *plausible* fit rather than a failure.

        The returns are drawn from a seeded RNG, not from sinusoids. Six
        sine waves over a three-month window are nowhere near orthogonal —
        the design matrix is nearly rank-deficient, the multivariate fit
        redistributes the loadings across collinear columns, and an asset
        built with a market beta of 0.6 came back at 0.91. Nothing errored;
        the number was simply wrong, which is the failure mode this whole
        fixture exists to make visible.

        The window ends before `DAY`. A generator running forward from the
        start date into September 2026 would put most observations after
        the `as_of` the tests query at, and a series truncated by an
        effective-date bound looks exactly like a series that was never
        written.
        """
        import numpy as np

        rng = np.random.default_rng(20260809)
        names = ("MKT_RF", "SMB", "HML", "RMW", "CMA", "MOM")
        # Each asset's true loading on each factor. Market betas fan out
        # 0.6 -> 1.8 so the test can assert recovery of a known number
        # rather than merely that a number appeared.
        loadings = np.array(
            [[0.6 + 0.3 * a] + [0.2 * ((a + f) % 3 - 1) for f in range(5)] for a in range(assets)]
        )
        rows: list[tuple[str, str, object, date]] = []
        for d in range(days):
            when = date(2025, 9, 1) + timedelta(days=d)
            draws = rng.normal(0.0, 0.010, size=len(names))
            for name, value in zip(names, draws, strict=True):
                rows.append((f"factor:{name}", "TOT_RETURN", float(value), when))
            rows.append(("factor:RF", "TOT_RETURN", 0.0001, when))
            idio = rng.normal(0.0, 0.0015, size=assets)
            for a in range(assets):
                rows.append(
                    (
                        f"portfolio:ASSET{a}",
                        "TOT_RETURN",
                        float(loadings[a] @ draws + idio[a] + 0.0001),
                        when,
                    )
                )
        return self._write("frenchdata", rows)

    def with_swaptions(self, count: int = 40) -> StoreBuilder:
        """Swaption prints a surface can be fitted through.

        All on one trading day, so the surface is built without pooling:
        pooling averages a fortnight of surfaces into one, and a fixture
        that needed it would be testing the pooling rather than the fit.

        **Needs `with_curves()`.** A premium is a price, and turning a
        price into a volatility needs a forward and a discount factor. On
        its own this method yields "40 prints read, none solvable" — which
        is the surface being honest, and was how the omission was found.
        """
        rows: list[tuple[str, str, object, date]] = []
        for i in range(count):
            expiry_years = (1, 2, 5)[i % 3]
            tenor_years = (5, 10)[i % 2]
            expiry = date(DAY.year + expiry_years, 7, 31)
            rows += [
                (f"swaption:EUR:P{i:04d}", "EXPIRY_DATE", expiry, DAY),
                (
                    f"swaption:EUR:P{i:04d}",
                    "UNDERLIER_MATURITY",
                    date(expiry.year + tenor_years, 7, 31),
                    DAY,
                ),
                (f"swaption:EUR:P{i:04d}", "STRIKE", 0.0325 + 0.0002 * (i % 5 - 2), DAY),
                (
                    f"swaption:EUR:P{i:04d}",
                    "PREMIUM_FRACTION",
                    0.030 + 0.001 * (i % 7 - 3),
                    DAY,
                ),
                (f"swaption:EUR:P{i:04d}", "PAYER", True, DAY),
                (f"swaption:EUR:P{i:04d}", "NOTIONAL_CAPPED", False, DAY),
            ]
        return self._write("dtcc-sdr", rows)


__all__ = ["DAY", "KNOWN", "LATER", "StoreBuilder"]
