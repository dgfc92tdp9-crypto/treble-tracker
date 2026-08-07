"""TVAL Prong 3: the residual layer (spec §15.4).

The issuer curve explains a bond's yield from its maturity. What it leaves
over — the residual — may be noise, or may carry structure the curve cannot
express: a small issue trading wide because nobody makes a market in it, an
old bond off the run, a high coupon that a par-fitted curve mismatches. This
learns that structure, **or reports that it could not**.

**The null is the model to beat, and beating it is not assumed.** Predicting
zero residual is a real model: it says the curve is already unbiased and
what is left is noise. A gradient-boosted tree on two hundred bonds and four
features will always fit the training set better than that, and will usually
do worse out of sample. :func:`fit_residual_model` therefore reports skill
as *out-of-sample* mean absolute error against the null's, and
:attr:`ResidualModel.is_useful` is false when it does not win. A layer that
cannot beat predicting zero must not be allowed to move a price.

This is the whole design. An ML layer that silently applies itself is the
most dangerous thing in an evaluated-pricing system: its output is
plausible by construction, it moves every number a little, and there is no
obvious symptom when it is wrong.

**K-fold, not a single split.** With this few bonds one split is a coin
toss — the measured skill would swing by more than the effect being
measured. Folds are seeded so the number is reproducible, because a skill
figure that changes between runs cannot be compared against the last one.

**Features are what the curve does not already use.** Maturity is excluded
deliberately: the curve is fitted on it, so a model given maturity would
relearn the curve's own shape and report skill that is really the curve's.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import KFold

from treble.analytics.registry import model

#: Fewest observations a residual model may be fitted on. Below this the
#: cross-validated skill is dominated by which bonds landed in which fold.
MIN_OBSERVATIONS = 40

#: Folds for the cross-validation, and the seed that makes it reproducible.
FOLDS = 5
SEED = 20260807

#: How much better than the null the model must be, out of sample, before
#: it is allowed to move a price. Not zero: a model that ties the null has
#: found nothing, and a hair's improvement on this few bonds is noise.
MIN_SKILL = 0.05


@dataclass(frozen=True)
class ResidualObservation:
    """One bond's residual against its issuer curve, with its features."""

    identifier: str
    #: Fitted yield less curve yield, in decimal. What is to be explained.
    residual: float
    #: Coupon, in decimal. A par-fitted curve mismatches high-coupon bonds.
    coupon: float
    #: Position size in USD — a proxy for issue size, and so for liquidity.
    size_usd: float
    #: Number of bonds the issuer has on the curve. A curve fitted through
    #: three points constrains a residual differently from one through ten.
    issuer_bond_count: int


@dataclass(frozen=True)
class ResidualModel:
    """A fitted residual model and, more importantly, whether it works."""

    #: Out-of-sample mean absolute error, in basis points.
    model_mae_bp: float
    #: The null's out-of-sample MAE — predicting zero residual — in bp.
    null_mae_bp: float
    observations: int
    #: Feature importances, in the order the features were built. Reported
    #: because a model whose skill rests entirely on one feature is a
    #: different claim from one that uses all of them.
    importances: tuple[float, ...]

    @property
    def skill(self) -> float:
        """Fractional improvement on the null. Negative means worse."""
        if self.null_mae_bp <= 0.0:
            return 0.0
        return (self.null_mae_bp - self.model_mae_bp) / self.null_mae_bp

    @property
    def is_useful(self) -> bool:
        """Whether this may be allowed to move a price.

        False is a perfectly good answer and the common one. It means the
        issuer curve is already doing the work and the residual is noise —
        which is information, not a failure.
        """
        return self.skill >= MIN_SKILL


class ResidualModelUnavailableError(RuntimeError):
    """Not enough to fit on, with the counts that led there."""


def _features(observations: Sequence[ResidualObservation]) -> np.ndarray:
    # Maturity is deliberately absent: the curve is fitted on it, so a model
    # given it would relearn the curve's shape and report the curve's skill
    # as its own.
    return np.array(
        [
            [o.coupon, np.log1p(max(o.size_usd, 0.0)), float(o.issuer_bond_count)]
            for o in observations
        ],
        dtype=float,
    )


@model(
    model_id="tval.residual_model",
    version="1.0",
    spec_section="§15.4",
    summary="Cross-validated residual model, with skill against the null",
)
def fit_residual_model(observations: Sequence[ResidualObservation]) -> ResidualModel:
    """Fit and, above all, measure.

    The returned object is mostly a measurement. Callers must consult
    `is_useful` before applying it: a model that cannot beat predicting
    zero out of sample has found nothing, and letting it move a price would
    add noise to an evaluated price while looking like sophistication.
    """
    if len(observations) < MIN_OBSERVATIONS:
        raise ResidualModelUnavailableError(
            f"{len(observations)} residuals is too few to measure skill on; "
            f"{MIN_OBSERVATIONS} is the fewest where cross-validated error is not "
            "dominated by which bonds landed in which fold"
        )

    features = _features(observations)
    targets = np.array([o.residual for o in observations], dtype=float)

    folds = KFold(n_splits=FOLDS, shuffle=True, random_state=SEED)
    model_errors: list[float] = []
    null_errors: list[float] = []
    for train, test in folds.split(features):
        estimator = GradientBoostingRegressor(
            n_estimators=100, max_depth=2, learning_rate=0.05, random_state=SEED
        )
        estimator.fit(features[train], targets[train])
        model_errors.extend(np.abs(estimator.predict(features[test]) - targets[test]))
        # The null predicts zero residual: the curve is unbiased and the
        # rest is noise. Not the training mean -- that would be a different,
        # weaker null, and an easier one to beat.
        null_errors.extend(np.abs(targets[test]))

    final = GradientBoostingRegressor(
        n_estimators=100, max_depth=2, learning_rate=0.05, random_state=SEED
    )
    final.fit(features, targets)

    return ResidualModel(
        model_mae_bp=float(np.mean(model_errors)) * 1e4,
        null_mae_bp=float(np.mean(null_errors)) * 1e4,
        observations=len(observations),
        importances=tuple(float(v) for v in final.feature_importances_),
    )


__all__ = [
    "FOLDS",
    "MIN_OBSERVATIONS",
    "MIN_SKILL",
    "ResidualModel",
    "ResidualModelUnavailableError",
    "ResidualObservation",
    "fit_residual_model",
]
