"""The `PORT` risk environment built from stored returns (spec §16.3).

`test_factors.py` proves the estimator is right. This proves the *inputs* are
right, which is a separate and less obvious failure: every refusal below
would otherwise produce a complete, plausible risk screen from the wrong
data, and none of them would look wrong on the screen itself.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.factor_model import (
    ASSET_NS,
    FACTOR_NS,
    FACTORS,
    RISK_FREE,
    FactorModelUnavailableError,
    build_factor_model,
    template_portfolio,
)

KNOWN = datetime(2026, 8, 1, 6, 0, tzinfo=UTC)
AS_OF = datetime(2026, 8, 6, 18, 0, tzinfo=UTC)
START = date(2024, 1, 1)


def _record() -> Provenance:
    return Provenance(
        source_system="frenchdata",
        source_uri="https://example.invalid/ff",
        retrieved_at=KNOWN,
        method=ExtractionMethod.BULK_FILE,
        extractor_version="1",
        payload_hash="0" * 64,
    )


def _store(
    tmp_path: Path,
    *,
    days: int = 400,
    assets: int = 4,
    risk_free: float | None = 0.0001,
    asset_days: int | None = None,
) -> DuckStore:
    """A store holding a synthetic but well-formed return panel."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = DuckStore(tmp_path / "t.db")
    record = _record()
    store.write_provenance([record])

    facts: list[Fact] = []

    def emit(subject: str, day: date, value: float) -> None:
        facts.append(
            Fact(
                subject=TUID(subject),
                field="TOT_RETURN",
                value=value,
                effective_from=day,
                effective_to=day,
                knowledge_from=KNOWN,
                provenance_id=record.id,
            )
        )

    for index in range(days):
        day = START + timedelta(days=index)
        for offset, factor in enumerate(FACTORS):
            emit(f"{FACTOR_NS}:{factor}", day, 0.001 * math.sin(index / (3 + offset)))
        if risk_free is not None:
            emit(f"{FACTOR_NS}:{RISK_FREE}", day, risk_free)
    for index in range(asset_days if asset_days is not None else days):
        day = START + timedelta(days=index)
        for asset in range(assets):
            emit(
                f"{ASSET_NS}:IND{asset}",
                day,
                0.001 * math.cos(index / (2 + asset)) + 0.0001 * asset,
            )

    store.write_facts(facts)
    return store


class TestItFits:
    def test_a_model_comes_back_over_the_stored_window(self, tmp_path: Path) -> None:
        fitted = build_factor_model(_store(tmp_path), as_of=AS_OF)
        assert fitted.covariance.factors == FACTORS
        assert len(fitted.exposures.assets) == 4
        assert fitted.observations == 400

    def test_the_window_is_the_most_recent_days_not_the_earliest(self, tmp_path: Path) -> None:
        """A risk model fitted on the *start* of the available history would
        describe a regime that has passed, and would go on doing so as new
        data arrived without the number ever moving."""
        fitted = build_factor_model(_store(tmp_path, days=400), as_of=AS_OF, window_days=100)
        assert fitted.observations == 100
        assert fitted.last_date == START + timedelta(days=399)
        assert fitted.first_date == START + timedelta(days=300)

    def test_the_risk_free_rate_is_not_one_of_the_factors(self, tmp_path: Path) -> None:
        """Regressing an asset on cash as though it were a factor would
        report a 'beta to the risk-free rate' and consume a degree of
        freedom to do it."""
        fitted = build_factor_model(_store(tmp_path), as_of=AS_OF)
        assert RISK_FREE not in fitted.covariance.factors

    def test_asset_returns_have_the_risk_free_rate_removed(self, tmp_path: Path) -> None:
        """The industry series are total returns and the factors are excess
        returns. Regressing one on the other without converting puts the
        short rate into every intercept — invisible on screen, and larger
        the higher rates are.

        Two fits over identical returns differing only in the risk-free rate
        must therefore produce the same betas and different alphas.
        """
        zero = build_factor_model(_store(tmp_path / "a", risk_free=0.0), as_of=AS_OF)
        positive = build_factor_model(_store(tmp_path / "b", risk_free=0.0002), as_of=AS_OF)
        assert zero.exposures.betas == pytest.approx(positive.exposures.betas, rel=1e-9)
        assert zero.exposures.alphas[0] != pytest.approx(positive.exposures.alphas[0], rel=1e-9)
        assert zero.exposures.alphas[0] - positive.exposures.alphas[0] == pytest.approx(
            0.0002, abs=1e-9
        )


class TestItRefusesRatherThanShowingAPlausibleScreen:
    def test_missing_factors_are_named(self, tmp_path: Path) -> None:
        store = DuckStore(tmp_path / "t.db")
        with pytest.raises(FactorModelUnavailableError, match="no return series for"):
            build_factor_model(store, as_of=AS_OF)

    def test_a_store_with_factors_but_no_assets_says_so(self, tmp_path: Path) -> None:
        """Distinct from having no factors: one means the source was never
        ingested, the other that there is nothing to hold. A single message
        would send the reader to the wrong fix."""
        store = _store(tmp_path, assets=0)
        with pytest.raises(FactorModelUnavailableError, match="nothing to hold"):
            build_factor_model(store, as_of=AS_OF)

    def test_too_short_a_window_is_refused(self, tmp_path: Path) -> None:
        store = _store(tmp_path, days=400)
        with pytest.raises(FactorModelUnavailableError, match="fewest a covariance"):
            build_factor_model(store, as_of=AS_OF, window_days=30)

    def test_assets_without_the_full_window_are_dropped_not_fitted_short(
        self, tmp_path: Path
    ) -> None:
        """An asset regressed over the days it happens to have would sit in
        the same table as its neighbours looking comparable, while describing
        a different period."""
        store = _store(tmp_path, days=400, asset_days=200)
        with pytest.raises(FactorModelUnavailableError, match="covers the full"):
            build_factor_model(store, as_of=AS_OF, window_days=300)

    def test_a_read_before_the_data_was_known_finds_nothing(self, tmp_path: Path) -> None:
        """I2 all the way down. A risk model that ignored `as_of` would
        answer 'what was the risk on Tuesday' with today's data."""
        store = _store(tmp_path)
        earlier = KNOWN - timedelta(days=1)
        with pytest.raises(FactorModelUnavailableError, match="no return series for"):
            build_factor_model(store, as_of=earlier)


class TestTheTemplatePortfolio:
    def test_it_is_equally_weighted_across_every_fitted_asset(self, tmp_path: Path) -> None:
        fitted = build_factor_model(_store(tmp_path), as_of=AS_OF)
        weights = template_portfolio(fitted)
        assert set(weights) == set(fitted.exposures.assets)
        assert sum(weights.values()) == pytest.approx(1.0)
        assert len(set(weights.values())) == 1

    def test_it_only_holds_assets_the_model_can_decompose(self, tmp_path: Path) -> None:
        """A weight on a name with no exposures would make `portfolio_risk`
        refuse — correctly, but for a portfolio this module built itself."""
        fitted = build_factor_model(_store(tmp_path), as_of=AS_OF)
        assert set(template_portfolio(fitted)) <= set(fitted.exposures.assets)
