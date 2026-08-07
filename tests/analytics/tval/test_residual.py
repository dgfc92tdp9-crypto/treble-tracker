"""TVAL's residual layer (spec §15.4).

The tests that matter are the ones about *not* being useful. An ML layer
that silently applies itself is the most dangerous thing in an
evaluated-pricing system: its output is plausible by construction, it moves
every number a little, and there is no obvious symptom when it is wrong. So
the suite spends most of its effort on pure noise, where the honest answer
is `is_useful is False`.
"""

from __future__ import annotations

import numpy as np
import pytest

from treble.analytics.tval.residual import (
    MIN_OBSERVATIONS,
    MIN_SKILL,
    ResidualModelUnavailableError,
    ResidualObservation,
    explained_residual_bp,
    fit_residual_model,
)

_fit = fit_residual_model.__wrapped__  # type: ignore[attr-defined]
_explain = explained_residual_bp.__wrapped__  # type: ignore[attr-defined]


def _noise(count: int = 120, seed: int = 1) -> list[ResidualObservation]:
    """Residuals with no relationship to the features at all."""
    rng = np.random.default_rng(seed)
    return [
        ResidualObservation(
            identifier=f"isin:{i:012d}",
            residual=float(rng.normal(0.0, 0.0020)),
            coupon=float(rng.uniform(0.02, 0.07)),
            size_usd=float(rng.uniform(1e5, 5e7)),
            issuer_bond_count=int(rng.integers(3, 12)),
        )
        for i in range(count)
    ]


def _signal(count: int = 200, seed: int = 2) -> list[ResidualObservation]:
    """Residuals that genuinely depend on size: small issues trade wide."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(count):
        size = float(rng.uniform(1e5, 5e7))
        wide = 0.0040 * (1.0 - np.log1p(size) / np.log1p(5e7))
        out.append(
            ResidualObservation(
                identifier=f"isin:{i:012d}",
                residual=float(wide + rng.normal(0.0, 0.0003)),
                coupon=float(rng.uniform(0.02, 0.07)),
                size_usd=size,
                issuer_bond_count=int(rng.integers(3, 12)),
            )
        )
    return out


class TestItRefusesToBeUsefulOnNoise:
    """The important half. `is_useful is False` is a good answer."""

    @pytest.mark.parametrize("seed", [1, 2, 3, 4])
    def test_pure_noise_produces_no_skill(self, seed: int) -> None:
        """A boosted tree will always fit noise in-sample. Out of sample it
        must not beat predicting zero, and this is the check that stops a
        plausible-looking layer from moving every evaluated price."""
        fitted = _fit(_noise(seed=seed))
        assert fitted.skill < MIN_SKILL
        assert fitted.is_useful is False

    def test_the_null_is_predicting_zero_not_the_training_mean(self) -> None:
        """A weaker null is an easier one to beat, and the difference only
        shows on *biased* residuals.

        The first version of this test used centred noise, where the
        training mean is ~0 and the two nulls coincide -- switching the
        source to `targets - train.mean()` left the whole suite passing. So
        these residuals sit 30bp wide of the curve. Against a zero null that
        bias is error the model must explain; against a mean null it is
        subtracted for free, which would let a biased curve look unbiased
        and hide exactly the defect the residual layer exists to surface.
        """
        rng = np.random.default_rng(7)
        biased = [
            ResidualObservation(
                identifier=f"isin:{i:012d}",
                residual=float(0.0030 + rng.normal(0.0, 0.0002)),
                coupon=float(rng.uniform(0.02, 0.07)),
                size_usd=float(rng.uniform(1e5, 5e7)),
                issuer_bond_count=int(rng.integers(3, 12)),
            )
            for i in range(120)
        ]
        fitted = _fit(biased)
        # Zero null: MAE is the full 30bp bias.
        assert fitted.null_mae_bp == pytest.approx(30.0, abs=1.5)
        # A training-mean null would score near the noise, ~2bp.
        assert fitted.null_mae_bp > 20.0


class TestItFindsRealStructure:
    def test_a_genuine_size_effect_is_learned(self) -> None:
        """Small issues trading wide is a real effect, and the layer exists
        to catch it. If this failed, the refusals above would be the model
        never working rather than the model being honest."""
        fitted = _fit(_signal())
        assert fitted.skill > MIN_SKILL
        assert fitted.is_useful is True

    def test_the_importances_name_the_feature_that_carried_it(self) -> None:
        """A model whose skill rests on one feature is a different claim
        from one that uses all three, and a screen should be able to say
        which. Size is the second feature."""
        fitted = _fit(_signal())
        assert fitted.importances[1] == max(fitted.importances)


class TestSkillIsMeasuredHonestly:
    def test_skill_is_out_of_sample(self) -> None:
        """In-sample a boosted tree would score near-perfectly on noise.
        A model MAE below the null on pure noise would mean the folds are
        leaking."""
        fitted = _fit(_noise())
        assert fitted.model_mae_bp > 0.0
        assert fitted.model_mae_bp >= fitted.null_mae_bp * 0.9

    def test_the_result_is_reproducible(self) -> None:
        """A skill figure that changes between runs cannot be compared with
        the last one, so the folds are seeded."""
        first, second = _fit(_noise()), _fit(_noise())
        assert first.model_mae_bp == pytest.approx(second.model_mae_bp)

    def test_skill_is_zero_when_the_null_is_perfect(self) -> None:
        """All-zero residuals make the null's error zero, and a fractional
        improvement on zero is undefined rather than infinite."""
        flat = [
            ResidualObservation(
                identifier=f"isin:{i:012d}",
                residual=0.0,
                coupon=0.04,
                size_usd=1e6,
                issuer_bond_count=5,
            )
            for i in range(60)
        ]
        assert _fit(flat).skill == 0.0


class TestItRefusesTooFew:
    def test_below_the_minimum_it_raises(self) -> None:
        """Cross-validated error on a handful of bonds is dominated by which
        of them landed in which fold."""
        with pytest.raises(ResidualModelUnavailableError, match="too few"):
            _fit(_noise(count=MIN_OBSERVATIONS - 1))


class TestTheSkillIsActuallyConsulted:
    """`is_useful` was computed and nothing read it -- the same shape as a
    flag that is set and never checked. A gate nobody passes through is not
    a gate."""

    def test_a_rejected_model_changes_nothing(self) -> None:
        fitted = _fit(_noise())
        assert fitted.is_useful is False
        left, why = _explain(fitted, 40.0)
        assert left == 40.0
        assert "rejected" in why

    def test_a_rejected_model_reads_differently_from_no_model(self) -> None:
        """Measured-and-rejected and never-fitted are different states, and
        a screen showing them alike would hide that somebody had looked."""
        _, rejected = _explain(_fit(_noise()), 40.0)
        _, absent = _explain(None, 40.0)
        assert rejected != absent
        assert "no residual model" in absent

    def test_a_useful_model_explains_its_skill_and_no_more(self) -> None:
        """Skill is fractional error reduction over a cross-validated
        average, not a per-bond prediction. Subtracting the whole residual
        would claim far more than that licenses."""
        fitted = _fit(_signal())
        assert fitted.is_useful is True
        left, why = _explain(fitted, 40.0)
        assert left == pytest.approx(40.0 * (1.0 - fitted.skill))
        assert 0.0 < left < 40.0
        assert "applied" in why

    def test_the_sign_of_the_residual_is_preserved(self) -> None:
        """A cheap bond must not be made dear by the adjustment."""
        fitted = _fit(_signal())
        left, _ = _explain(fitted, -40.0)
        assert left < 0.0
