"""TFM3 v1 — a multi-factor risk model on published factor returns (spec §16.3).

The mathematics §16.3 asks for is standard and small:

    sigma_p^2 = w' (B Sigma_f B' + D) w

with `B` the N x K exposure matrix, `Sigma_f` the K x K factor covariance and
`D` the diagonal of specific variances. What made it unbuildable here was not
the algebra but `Sigma_f`: estimating a factor covariance needs a factor
*return panel*, and the store had none. `treble/ingest/frenchdata.py` supplies
one — Fama/French factors daily since 1963, and industry portfolios whose own
return series make them usable as assets.

**What this is not.** §16.2 describes TFM3 as 1,500+ factors recalculated
daily. This is six, and the gap is data rather than implementation: the
factors here are the ones published, and a per-name equity panel — which the
store still does not have — is what a wider model would need. Calling six
factors TFM3 without saying so would be the more comfortable claim and the
wrong one.

**Why exposures are estimated rather than assumed.** A risk model whose
exposures are asserted (an industry mapping, a sector dummy) reports the risk
of the classification rather than of the holdings. Here every asset's `B` row
comes from a time-series regression of its own excess returns on the factors,
so an asset that stopped behaving like its label is a defect the model can
see. The regression's residual variance is the specific risk in `D`, which is
the same fit and cannot drift from it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np

from treble.analytics.registry import model

#: Trading days per year, for annualising a daily-frequency model. 252 is the
#: convention; it is named here rather than inlined because a model that
#: annualised with 365 would report volatility ~20% too high and still look
#: entirely plausible on screen.
TRADING_DAYS: int = 252

#: The fewest observations that may back a covariance. Well below this the
#: sample covariance is singular or near-singular and its inverse — which any
#: optimiser will want — is meaningless. Refused rather than returned, because
#: a risk number computed from too little data is indistinguishable on screen
#: from one computed from enough.
MIN_OBSERVATIONS: int = 60


@dataclass(frozen=True)
class ReturnPanel:
    """Aligned daily returns: `dates` x `names`, no gaps.

    Alignment is the whole content of this type. Factor and asset returns
    arriving from different files cover different calendars, and regressing
    one on the other with mismatched dates silently pairs an asset's Tuesday
    with a factor's Wednesday — which produces a plausible, wrong beta.
    """

    names: tuple[str, ...]
    dates: tuple[date, ...]
    values: np.ndarray  # (T, N)

    def __post_init__(self) -> None:
        if self.values.shape != (len(self.dates), len(self.names)):
            raise ValueError(
                f"panel is {self.values.shape} but has {len(self.dates)} dates and "
                f"{len(self.names)} names"
            )
        if not np.isfinite(self.values).all():
            raise ValueError("panel contains non-finite returns; align and drop before building")

    @property
    def observations(self) -> int:
        return len(self.dates)

    def column(self, name: str) -> np.ndarray:
        return self.values[:, self.names.index(name)]

    @classmethod
    def aligned(
        cls,
        series: Mapping[str, Mapping[date, float]],
        *,
        names: Sequence[str] | None = None,
    ) -> ReturnPanel:
        """Build a panel from per-series date->return maps, on their common dates.

        A constructor rather than a free function, and deliberately: the I3
        registry walk requires every public callable in `treble.analytics` to
        carry a model envelope, and this one produces no number a user sees —
        it prepares an input. Stamping a model id on it would put model
        identity on data preparation, which is the same mistake `_survival`
        avoided in the CDS pricer. Making it a named constructor says what it
        is instead of exempting it from the rule.

        Intersection, not union-with-fill. Filling a missing return with zero
        would assert that the asset did not move, which is a return
        observation the source never made — and zeros pull correlations
        toward zero, so the model would systematically *understate* risk.
        Dropping the day loses information; inventing it produces a number
        that is wrong in a known direction.
        """
        chosen = tuple(names) if names is not None else tuple(sorted(series))
        missing = [n for n in chosen if n not in series]
        if missing:
            raise KeyError(f"no return series for: {', '.join(missing)}")
        if not chosen:
            raise ValueError("a panel needs at least one series")

        common: set[date] | None = None
        for name in chosen:
            days = set(series[name])
            common = days if common is None else (common & days)
        ordered = tuple(sorted(common or ()))
        if not ordered:
            raise ValueError(
                f"the {len(chosen)} series share no common dates; a panel cannot be aligned"
            )
        values = np.array([[series[name][day] for name in chosen] for day in ordered], dtype=float)
        return cls(names=chosen, dates=ordered, values=values)


@dataclass(frozen=True)
class FactorCovariance:
    """`Sigma_f` and what it was estimated from."""

    factors: tuple[str, ...]
    matrix: np.ndarray  # (K, K), daily
    observations: int
    first_date: date
    last_date: date
    half_life_days: int | None

    def annualised(self) -> np.ndarray:
        return self.matrix * TRADING_DAYS

    def volatility(self, factor: str) -> float:
        """Annualised standard deviation of one factor."""
        index = self.factors.index(factor)
        return float(math.sqrt(self.matrix[index, index] * TRADING_DAYS))

    def correlation(self) -> np.ndarray:
        deviations = np.sqrt(np.diag(self.matrix))
        result: np.ndarray = self.matrix / np.outer(deviations, deviations)
        return result


@model(
    model_id="risk.factor_covariance",
    version="1.0",
    spec_section="§16.3",
    summary="K x K factor covariance from a daily factor return panel",
)
def factor_covariance(panel: ReturnPanel, *, half_life_days: int | None = None) -> FactorCovariance:
    """Sample covariance of the factor returns, optionally exponentially weighted.

    `half_life_days` weights recent observations more heavily, which is what
    makes a risk model respond to a change in regime rather than reporting a
    decade's average through a crisis. Left unset it is the equal-weighted
    sample covariance, and the difference between the two is visible rather
    than buried: both are reachable, and the choice is stamped on the result.

    Means are removed. Not removing them would fold the factors' realised
    drift into their covariance — over a long sample that is a small error,
    and over a short one it is not.
    """
    if panel.observations < MIN_OBSERVATIONS:
        raise ValueError(
            f"{panel.observations} observations is too few for a covariance over "
            f"{len(panel.names)} factors (minimum {MIN_OBSERVATIONS}); the estimate would be "
            "singular or near-singular and its inverse meaningless"
        )
    if half_life_days is not None and half_life_days <= 0:
        raise ValueError("half-life must be positive; a non-positive one weights the future")

    values = panel.values
    if half_life_days is None:
        weights = np.full(panel.observations, 1.0 / panel.observations)
    else:
        decay = math.log(2.0) / half_life_days
        age = np.arange(panel.observations - 1, -1, -1, dtype=float)
        raw = np.exp(-decay * age)
        weights = raw / raw.sum()

    mean = weights @ values
    centred = values - mean
    # Weighted covariance with the unbiasing factor for weighted samples;
    # for equal weights this reduces to the familiar 1/(T-1).
    scale = 1.0 - float(np.sum(weights**2))
    matrix = (centred * weights[:, None]).T @ centred / scale
    # Symmetrise: the product above is symmetric in exact arithmetic and
    # differs in the last bits in floating point, which is enough to make a
    # Cholesky factorisation fail on a matrix that is positive definite.
    matrix = (matrix + matrix.T) / 2.0

    return FactorCovariance(
        factors=panel.names,
        matrix=matrix,
        observations=panel.observations,
        first_date=panel.dates[0],
        last_date=panel.dates[-1],
        half_life_days=half_life_days,
    )


@dataclass(frozen=True)
class Exposures:
    """`B` and `D`: what each asset's returns say about its factor loadings."""

    assets: tuple[str, ...]
    factors: tuple[str, ...]
    betas: np.ndarray  # (N, K)
    alphas: np.ndarray  # (N,) daily intercepts
    specific_variance: np.ndarray  # (N,) daily residual variance — the diagonal of D
    r_squared: np.ndarray  # (N,)
    observations: int

    def beta(self, asset: str, factor: str) -> float:
        return float(self.betas[self.assets.index(asset), self.factors.index(factor)])

    def specific_volatility(self, asset: str) -> float:
        """Annualised idiosyncratic volatility."""
        return float(math.sqrt(self.specific_variance[self.assets.index(asset)] * TRADING_DAYS))


@model(
    model_id="risk.factor_exposures",
    version="1.0",
    spec_section="§16.3",
    summary="Asset factor exposures and specific risk by time-series regression",
)
def estimate_exposures(assets: ReturnPanel, factors: ReturnPanel) -> Exposures:
    """Regress each asset's returns on the factors: one OLS fit per asset.

    The two panels must already be aligned on the same dates. This is checked
    rather than assumed — regressing an asset on a factor series that covers
    different days pairs the wrong observations and returns a beta that is
    wrong without being obviously wrong.

    Specific variance is the residual variance from this same fit, so `B` and
    `D` cannot describe different regressions. Computing them separately is
    the ordinary way to get a risk model whose parts do not add up.
    """
    if assets.dates != factors.dates:
        raise ValueError(
            f"asset panel covers {len(assets.dates)} dates and the factor panel "
            f"{len(factors.dates)}; regressing across mismatched calendars pairs an "
            "asset's day with a factor's other day"
        )
    if assets.observations < MIN_OBSERVATIONS:
        raise ValueError(
            f"{assets.observations} observations is too few to estimate exposures "
            f"(minimum {MIN_OBSERVATIONS})"
        )

    observations = assets.observations
    design = np.column_stack([np.ones(observations), factors.values])
    # lstsq rather than a normal-equation inverse: the factors are correlated
    # by construction (HML and CMA especially), and the normal equations lose
    # roughly twice the precision on an ill-conditioned design.
    coefficients, *_ = np.linalg.lstsq(design, assets.values, rcond=None)
    fitted = design @ coefficients
    residuals = assets.values - fitted

    degrees_of_freedom = observations - design.shape[1]
    if degrees_of_freedom <= 0:
        raise ValueError(
            f"{observations} observations cannot fit {design.shape[1]} coefficients; "
            "the residual variance would be undefined or negative"
        )
    specific = np.sum(residuals**2, axis=0) / degrees_of_freedom

    demeaned = assets.values - assets.values.mean(axis=0)
    total = np.sum(demeaned**2, axis=0)
    # An asset that never moved has no variance to explain; reporting R^2 = 1
    # would say the model explained it perfectly.
    r_squared = np.where(total > 0, 1.0 - np.sum(residuals**2, axis=0) / total, np.nan)

    return Exposures(
        assets=assets.names,
        factors=factors.names,
        betas=coefficients[1:].T,
        alphas=coefficients[0],
        specific_variance=specific,
        r_squared=r_squared,
        observations=observations,
    )


@dataclass(frozen=True)
class RiskDecomposition:
    """A portfolio's ex-ante risk, split the way §16.3 requires."""

    total_volatility: float  # annualised
    factor_volatility: float
    specific_volatility: float
    #: Per-factor contribution to *variance*, summing (with specific) to the
    #: total. Contributions to volatility do not add up, and presenting them
    #: as though they did is the standard way a risk report lies.
    factor_contributions: tuple[tuple[str, float], ...]
    #: d(sigma_p)/d(w_i): what one more unit of each asset does to risk.
    marginal_contributions: tuple[tuple[str, float], ...]

    @property
    def factor_share(self) -> float:
        total = self.total_volatility**2
        return self.factor_volatility**2 / total if total > 0 else 0.0


@model(
    model_id="risk.portfolio_risk",
    version="1.0",
    spec_section="§16.3",
    summary="Ex-ante portfolio volatility decomposed into factor and specific risk",
)
def portfolio_risk(
    weights: Mapping[str, float], exposures: Exposures, covariance: FactorCovariance
) -> RiskDecomposition:
    """`sigma_p^2 = w'(B Sigma_f B' + D)w`, decomposed.

    Weights are given per asset by name rather than as a bare vector, because
    a vector silently misaligned with the exposure matrix produces a complete,
    plausible risk report for a portfolio nobody holds. A name not in the
    model is refused for the same reason: dropping it would report the risk of
    a smaller portfolio.

    Weights are not normalised. A book that does not sum to one is usually
    long/short or partially invested, and rescaling it would report a
    different portfolio's risk.
    """
    if covariance.factors != exposures.factors:
        raise ValueError(
            f"covariance is over {covariance.factors} and exposures over "
            f"{exposures.factors}; B Sigma_f B' would multiply mismatched factors"
        )
    unknown = sorted(set(weights) - set(exposures.assets))
    if unknown:
        raise ValueError(
            f"no exposures for: {', '.join(unknown)}. Dropping them would report the risk "
            "of a portfolio that is missing those positions rather than refusing"
        )

    w = np.array([weights.get(name, 0.0) for name in exposures.assets], dtype=float)
    if not np.any(w):
        raise ValueError("an empty portfolio has no risk to decompose")

    portfolio_betas = exposures.betas.T @ w  # (K,)
    factor_variance = float(portfolio_betas @ covariance.matrix @ portfolio_betas)
    specific_variance = float(np.sum(w**2 * exposures.specific_variance))
    total_variance = factor_variance + specific_variance

    annual = TRADING_DAYS
    total_volatility = math.sqrt(total_variance * annual)

    # Contribution of factor k to variance: b_k * (Sigma_f b)_k. These sum to
    # the factor variance exactly, which the tests assert.
    products = covariance.matrix @ portfolio_betas
    contributions = tuple(
        (name, float(portfolio_betas[i] * products[i]) * annual)
        for i, name in enumerate(covariance.factors)
    )

    total_covariance = exposures.betas @ covariance.matrix @ exposures.betas.T + np.diag(
        exposures.specific_variance
    )
    marginal = (
        (total_covariance @ w) * annual / total_volatility if total_volatility > 0 else w * 0.0
    )
    marginals = tuple((name, float(marginal[i])) for i, name in enumerate(exposures.assets))

    return RiskDecomposition(
        total_volatility=total_volatility,
        factor_volatility=math.sqrt(factor_variance * annual),
        specific_volatility=math.sqrt(specific_variance * annual),
        factor_contributions=contributions,
        marginal_contributions=marginals,
    )


__all__ = [
    "MIN_OBSERVATIONS",
    "TRADING_DAYS",
    "Exposures",
    "FactorCovariance",
    "ReturnPanel",
    "RiskDecomposition",
    "estimate_exposures",
    "factor_covariance",
    "portfolio_risk",
]
