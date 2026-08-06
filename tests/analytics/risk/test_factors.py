"""TFM3 v1 — the factor risk model (spec §16.3).

Three kinds of check, and the distinction matters:

- **Recovery.** Returns are simulated from known betas and a known factor
  covariance, and the estimator must recover them. This is the only test that
  can prove the algebra is right, because it is the only one with a true
  answer to compare against.
- **Identities.** The decomposition's parts must sum to its whole. These would
  pass on a wrong-but-self-consistent model, which is exactly why they are not
  the only tests here.
- **External behaviour.** Fitted on Fama/French's published returns and asked
  to predict a period it never saw. A sample covariance predicts the *fit
  period's* volatility, so the error must track the change in volatility
  regime — in direction and roughly in size. An implementation with a broken
  alignment, annualisation or covariance would not reproduce that
  relationship, and would still pass every identity above.
"""

from __future__ import annotations

import zipfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from treble.analytics.risk.factors import (
    MIN_OBSERVATIONS,
    TRADING_DAYS,
    ReturnPanel,
    estimate_exposures,
    factor_covariance,
    portfolio_risk,
)
from treble.ingest.frenchdata import parse_french_csv

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "frenchdata"

# The registry wraps these in the I3 envelope; the maths is the __wrapped__
# function. Tested directly here, with `test_i3_registry.py` covering the
# envelope itself, so a failure names one or the other.
_covariance = factor_covariance.__wrapped__
_exposures = estimate_exposures.__wrapped__
_risk = portfolio_risk.__wrapped__


def _days(count: int) -> list[date]:
    start = date(2020, 1, 1)
    return [start + timedelta(days=i) for i in range(count)]


def _simulated(
    *, observations: int = 2000, seed: int = 11
) -> tuple[ReturnPanel, ReturnPanel, np.ndarray, np.ndarray]:
    """(assets, factors, true betas, true specific vols) from a known model."""
    rng = np.random.default_rng(seed)
    factor_names = ("MKT", "SMB", "HML")
    # A deliberately correlated factor set: uncorrelated factors would let a
    # normal-equations solve look fine when it is not.
    chol = np.array([[0.010, 0.0, 0.0], [0.004, 0.006, 0.0], [-0.003, 0.002, 0.005]])
    factor_returns = rng.standard_normal((observations, 3)) @ chol.T

    # D is deliberately exposed to nothing: it exists so the decomposition
    # can be asked what it attributes when there is genuinely nothing there.
    betas = np.array([[1.0, 0.2, -0.3], [0.7, -0.4, 0.6], [1.3, 0.9, 0.1], [0.0, 0.0, 0.0]])
    specific_vol = np.array([0.004, 0.006, 0.010, 0.002])
    noise = rng.standard_normal((observations, 4)) * specific_vol
    asset_returns = factor_returns @ betas.T + noise

    days = tuple(_days(observations))
    factors = ReturnPanel(names=factor_names, dates=days, values=factor_returns)
    assets = ReturnPanel(names=("A", "B", "C", "D"), dates=days, values=asset_returns)
    return assets, factors, betas, specific_vol


class TestItRecoversAModelItWasGiven:
    """The only tests with a known true answer."""

    def test_betas_come_back(self) -> None:
        """Compared against each coefficient's own standard error rather than
        a flat tolerance. A fixed `atol` is a guess that has to be loosened
        whenever the simulation changes; four standard errors is a statement
        about the estimator that stays true as the noise, sample length or
        factor correlation change — and still fails a biased one."""
        assets, factors, betas, _ = _simulated()
        estimated = _exposures(assets, factors)

        design = np.column_stack([np.ones(assets.observations), factors.values])
        unscaled = np.linalg.inv(design.T @ design)
        # Var(beta_hat) = sigma_eps^2 (X'X)^-1; drop the intercept row/column.
        errors = np.sqrt(np.outer(estimated.specific_variance, np.diag(unscaled)[1:]))
        deviations = np.abs(estimated.betas - betas) / errors
        assert deviations.max() < 4.0, (
            f"a beta sits {deviations.max():.1f} standard errors from its true value:\n"
            f"{estimated.betas}\nvs\n{betas}"
        )

    def test_specific_risk_comes_back(self) -> None:
        """`D` is the residual variance of the same fit that produced `B`.
        A model whose specific risk is estimated separately can report parts
        that do not add up to its own total."""
        assets, factors, _, specific_vol = _simulated()
        estimated = _exposures(assets, factors)
        assert np.allclose(np.sqrt(estimated.specific_variance), specific_vol, rtol=0.10)

    def test_the_factor_covariance_comes_back(self) -> None:
        _, factors, _, _ = _simulated()
        covariance = _covariance(factors)
        truth = np.cov(factors.values, rowvar=False)
        assert np.allclose(covariance.matrix, truth, rtol=1e-9, atol=1e-12)

    def test_an_asset_with_no_factor_exposure_is_all_specific_risk(self) -> None:
        """Asset D was simulated with zero betas. A model that attributed
        risk to factors here would be finding structure in noise."""
        assets, factors, _, _ = _simulated()
        exposures = _exposures(assets, factors)
        covariance = _covariance(factors)
        decomposition = _risk({"D": 1.0}, exposures, covariance)
        assert decomposition.factor_share < 0.05, (
            f"a zero-beta asset was given {decomposition.factor_share:.1%} factor risk"
        )


class TestTheDecompositionAddsUp:
    def test_factor_and_specific_variance_sum_to_total(self) -> None:
        assets, factors, _, _ = _simulated()
        exposures, covariance = _exposures(assets, factors), _covariance(factors)
        result = _risk({"A": 0.5, "B": 0.3, "C": 0.2}, exposures, covariance)
        assert result.total_volatility**2 == pytest.approx(
            result.factor_volatility**2 + result.specific_volatility**2, rel=1e-12
        )

    def test_per_factor_contributions_sum_to_the_factor_variance(self) -> None:
        """Contributions to *variance*, which add up. Contributions to
        volatility do not, and presenting them as though they did is a
        standard way for a risk report to mislead."""
        assets, factors, _, _ = _simulated()
        exposures, covariance = _exposures(assets, factors), _covariance(factors)
        result = _risk({"A": 0.4, "B": 0.4, "C": 0.2}, exposures, covariance)
        total = sum(value for _, value in result.factor_contributions)
        assert total == pytest.approx(result.factor_volatility**2, rel=1e-10)

    def test_marginal_contributions_recover_total_risk_by_euler(self) -> None:
        """Volatility is homogeneous of degree one in the weights, so
        `sum(w_i * d(sigma)/d(w_i)) == sigma`. This is what makes marginal
        contributions attributable rather than merely indicative."""
        assets, factors, _, _ = _simulated()
        exposures, covariance = _exposures(assets, factors), _covariance(factors)
        weights = {"A": 0.5, "B": 0.2, "C": 0.2, "D": 0.1}
        result = _risk(weights, exposures, covariance)
        euler = sum(weights.get(name, 0.0) * value for name, value in result.marginal_contributions)
        assert euler == pytest.approx(result.total_volatility, rel=1e-10)

    def test_risk_matches_a_direct_computation_from_the_weights(self) -> None:
        """The decomposition must agree with the portfolio's own return
        series — same weights, same data, no factor model in the path."""
        assets, factors, _, _ = _simulated(observations=4000)
        exposures, covariance = _exposures(assets, factors), _covariance(factors)
        weights = {"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1}
        predicted = _risk(weights, exposures, covariance).total_volatility
        vector = np.array([weights[n] for n in assets.names])
        realised = float(np.std(assets.values @ vector, ddof=1) * np.sqrt(TRADING_DAYS))
        assert predicted == pytest.approx(realised, rel=0.05)


class TestItRefusesRatherThanGuesses:
    def test_a_panel_is_aligned_on_common_dates_not_filled(self) -> None:
        """Filling a gap with zero asserts the asset did not move — a return
        the source never reported — and zeros pull correlations toward zero,
        so the model would understate risk in a known direction."""
        panel = ReturnPanel.aligned(
            {
                "X": {date(2024, 1, 1): 0.01, date(2024, 1, 2): 0.02, date(2024, 1, 3): 0.03},
                "Y": {date(2024, 1, 2): -0.01, date(2024, 1, 3): 0.00},
            }
        )
        assert panel.dates == (date(2024, 1, 2), date(2024, 1, 3))
        assert panel.observations == 2

    def test_series_with_no_overlap_are_refused(self) -> None:
        with pytest.raises(ValueError, match="share no common dates"):
            ReturnPanel.aligned(
                {"X": {date(2024, 1, 1): 0.01}, "Y": {date(2025, 1, 1): 0.02}},
            )

    def test_too_few_observations_is_refused_not_returned(self) -> None:
        days = tuple(_days(MIN_OBSERVATIONS - 1))
        panel = ReturnPanel(
            names=("A", "B"),
            dates=days,
            values=np.random.default_rng(1).standard_normal((len(days), 2)) * 0.01,
        )
        with pytest.raises(ValueError, match="too few"):
            _covariance(panel)

    def test_mismatched_calendars_are_refused(self) -> None:
        """The failure this prevents is silent: regressing an asset on a
        factor series covering different days pairs the wrong observations
        and returns a beta that is wrong without looking wrong."""
        assets, factors, _, _ = _simulated(observations=200)
        shifted = ReturnPanel(
            names=factors.names,
            dates=tuple(d + timedelta(days=1) for d in factors.dates),
            values=factors.values,
        )
        with pytest.raises(ValueError, match="mismatched calendars"):
            _exposures(assets, shifted)

    def test_a_holding_with_no_exposures_is_refused(self) -> None:
        """Dropping it would report the risk of a portfolio missing that
        position, which is a smaller number and a plausible one."""
        assets, factors, _, _ = _simulated(observations=200)
        exposures, covariance = _exposures(assets, factors), _covariance(factors)
        with pytest.raises(ValueError, match="no exposures for"):
            _risk({"A": 0.5, "NOTMODELLED": 0.5}, exposures, covariance)

    def test_an_empty_portfolio_is_refused(self) -> None:
        assets, factors, _, _ = _simulated(observations=200)
        exposures, covariance = _exposures(assets, factors), _covariance(factors)
        with pytest.raises(ValueError, match="no risk to decompose"):
            _risk({"A": 0.0}, exposures, covariance)

    def test_a_covariance_is_symmetric_and_positive_semidefinite(self) -> None:
        """An asymmetric or indefinite covariance produces negative variances
        for some portfolio — a risk report with an imaginary volatility in
        it — and Cholesky is what any optimiser downstream will attempt."""
        _, factors, _, _ = _simulated()
        matrix = _covariance(factors).matrix
        assert np.array_equal(matrix, matrix.T)
        assert np.linalg.eigvalsh(matrix).min() > 0


def _french_series(archive: str) -> dict[str, dict[date, float]]:
    with zipfile.ZipFile(FIXTURES / archive) as zipped:
        text = zipped.read(zipped.namelist()[0]).decode("latin-1")
    columns, rows = parse_french_csv(text)
    return {
        name: {day: values[i] for day, values in rows if values[i] is not None}
        for i, name in enumerate(columns)
    }


@pytest.fixture(scope="module")
def published() -> tuple[dict[str, dict[date, float]], dict[str, dict[date, float]]]:
    """(factor excess returns, industry excess returns) from the fixtures."""
    five = _french_series("F-F_Research_Data_5_Factors_2x3_daily_CSV.zip")
    momentum = _french_series("F-F_Momentum_Factor_daily_CSV.zip")
    industries = _french_series("49_Industry_Portfolios_daily_CSV.zip")
    riskfree = five.pop("RF")
    factors = {**five, **momentum}
    # Industry series are total returns; the model regresses excess returns,
    # and mixing the two puts the risk-free rate into every beta.
    excess = {
        name: {day: value - riskfree[day] for day, value in series.items() if day in riskfree}
        for name, series in industries.items()
    }
    return factors, excess


@pytest.mark.golden
class TestAgainstPublishedReturns:
    """Fitted on Fama/French's own data — the panel that unblocked this."""

    def test_the_published_factors_are_all_present(self, published) -> None:  # type: ignore[no-untyped-def]
        factors, industries = published
        assert set(factors) == {"MKT_RF", "SMB", "HML", "RMW", "CMA", "MOM"}
        assert len(industries) == 49

    def test_factor_volatilities_are_in_their_published_range(self, published) -> None:  # type: ignore[no-untyped-def]
        """The market factor sits near 15-25% annualised and the style
        factors below it. Wrong annualisation is the error this catches: a
        model using 365 days rather than 252 reports ~20% too high, which is
        still a plausible-looking number."""
        factors, _ = published
        covariance = _covariance(ReturnPanel.aligned(factors))
        market = covariance.volatility("MKT_RF")
        assert 0.10 < market < 0.35, f"market volatility {market:.1%} is not a market"
        for style in ("SMB", "HML", "RMW", "CMA"):
            assert covariance.volatility(style) < market, (
                f"{style} at {covariance.volatility(style):.1%} exceeds the market"
            )

    def test_industries_are_mostly_explained_by_six_factors(self, published) -> None:  # type: ignore[no-untyped-def]
        """Median R^2 well above a half. Far below would mean the regression
        is not finding the market in industry returns, which would be a bug
        in the alignment rather than a fact about markets."""
        factors, industries = published
        factor_panel = ReturnPanel.aligned(factors)
        usable = {
            name: series
            for name, series in industries.items()
            if set(factor_panel.dates) <= set(series)
        }
        asset_panel = ReturnPanel.aligned(usable, names=tuple(sorted(usable)))
        factor_panel = ReturnPanel.aligned(factors, names=factor_panel.names)
        exposures = _exposures(asset_panel, factor_panel)
        assert float(np.median(exposures.r_squared)) > 0.5

    def test_prediction_error_tracks_the_volatility_regime(self, published) -> None:  # type: ignore[no-untyped-def]
        """The structural check, and the one a broken implementation fails.

        A sample covariance predicts the volatility of the period it was
        fitted on. So when the fit window is more volatile than the test
        window the model must over-predict, and by roughly the ratio of the
        two windows' volatilities. Measured across three decades of the full
        published history, predicted/realised tracked fit/test market
        volatility closely: 1.187 against 1.102, 0.858 against 0.831, and
        1.634 against 1.671.

        The fixture holds one window, so this asserts the relationship on it
        rather than the three-decade sweep. An implementation with a broken
        calendar alignment, a wrong annualisation or a mis-scaled covariance
        would pass every identity test above and fail this.
        """
        factors, industries = published
        factor_panel = ReturnPanel.aligned(factors)
        usable = {
            name: series
            for name, series in industries.items()
            if set(factor_panel.dates) <= set(series)
        }
        days = list(factor_panel.dates)
        split = len(days) // 2
        fit, test = set(days[:split]), set(days[split:])

        def window(source: dict[str, dict[date, float]], keep: set[date]):  # type: ignore[no-untyped-def]
            return {n: {d: v for d, v in s.items() if d in keep} for n, s in source.items()}

        fit_factors = ReturnPanel.aligned(window(factors, fit))
        fit_assets = ReturnPanel.aligned(window(usable, fit), names=tuple(sorted(usable)))
        covariance = _covariance(fit_factors)
        exposures = _exposures(fit_assets, fit_factors)

        regime = float(
            np.std(fit_factors.column("MKT_RF"), ddof=1)
            / np.std(ReturnPanel.aligned(window(factors, test)).column("MKT_RF"), ddof=1)
        )

        rng = np.random.default_rng(7)
        ratios = []
        for _ in range(20):
            picks = tuple(rng.choice(np.array(exposures.assets), size=10, replace=False))
            raw = rng.random(10)
            weights = dict(zip(picks, raw / raw.sum(), strict=True))
            predicted = _risk(weights, exposures, covariance).total_volatility
            out = ReturnPanel.aligned(window(usable, test), names=picks)
            realised = float(
                np.std(out.values @ np.array([weights[p] for p in picks]), ddof=1)
                * np.sqrt(TRADING_DAYS)
            )
            ratios.append(predicted / realised)

        observed = float(np.median(ratios))
        assert observed == pytest.approx(regime, rel=0.30), (
            f"predicted/realised was {observed:.3f} but the volatility regime moved by "
            f"{regime:.3f}. The model should mis-predict by about the regime change and "
            "no more; a larger gap is the model rather than the market."
        )
