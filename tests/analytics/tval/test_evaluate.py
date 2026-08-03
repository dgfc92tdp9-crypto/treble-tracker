"""`TVAL` — evaluated pricing and the score (spec §15).

The number matters less here than what surrounds it. A price with a low
score and a visible derivation is usable; a price nobody can explain is not,
and a wrong price presented confidently is the failure this system exists to
refuse. So most of these are about the *score* and the *refusals*.
"""

from __future__ import annotations

from datetime import date

import pytest

from treble.analytics.registry import ModelResult
from treble.analytics.tval.evaluate import (
    FairValueLevel,
    ObservationKind,
    PriceObservation,
    TvalPrice,
    UnpriceableError,
    Weighting,
    evaluate_price,
)

AS_OF = date(2026, 8, 3)
QUARTER_END = date(2026, 6, 30)


def observation(
    price: float,
    kind: ObservationKind = ObservationKind.EXECUTABLE_QUOTE,
    source: str = "Dealer A",
    observed_at: date = AS_OF,
    size: float | None = None,
) -> PriceObservation:
    return PriceObservation(
        price=price, kind=kind, source=source, observed_at=observed_at, size=size
    )


def price_of(*observations: PriceObservation, **kwargs: object) -> TvalPrice:
    return evaluate_price(observations, as_of=AS_OF, **kwargs).value  # type: ignore[arg-type]


class TestTheWeighting:
    def test_a_firm_quote_today_outweighs_a_stale_mark(self) -> None:
        """The whole point of the weighting. A quarter-old fund valuation
        must not drag a price away from what a dealer will trade at now."""
        result = price_of(
            observation(98.60, ObservationKind.EXECUTABLE_QUOTE, "Dealer A"),
            observation(95.00, ObservationKind.REPORTED_MARK, "Fund A", QUARTER_END),
        )
        assert result.price == pytest.approx(98.60, abs=0.05)

    def test_recency_decays_by_half_life(self) -> None:
        weighting = Weighting(half_life_days=5.0)
        result = price_of(
            observation(100.0, observed_at=AS_OF),
            observation(90.0, observed_at=date(2026, 7, 29)),  # 5 days = one half-life
            weighting=weighting,
        )
        fresh, stale = result.observations[0], result.observations[1]
        assert stale.recency_weight == pytest.approx(0.5)
        assert fresh.recency_weight == pytest.approx(1.0)

    def test_size_lifts_weight_with_diminishing_returns(self) -> None:
        """A ten-times-larger trade is about three times the evidence, not
        ten times — otherwise one block print would set the price alone."""
        small = price_of(observation(100.0, size=100_000.0)).observations[0]
        large = price_of(observation(100.0, size=1_000_000.0)).observations[0]
        assert large.size_weight > small.size_weight
        assert large.size_weight / small.size_weight < 4.0

    def test_absent_size_is_not_zero_weight(self) -> None:
        """A level published without size is weaker evidence, not evidence
        of a trade in nothing."""
        assert price_of(observation(100.0, size=None)).observations[0].size_weight > 0.0

    def test_the_weighting_is_a_parameter_not_a_constant(self) -> None:
        """§15.3: a user must be able to run the same machinery with their
        own weights and get their own valuation. If the weights were baked
        in, independent price verification would be impossible by
        construction."""
        marks_matter_more = Weighting(
            kind_weight={
                ObservationKind.EXECUTABLE_QUOTE: 0.1,
                ObservationKind.REPORTED_MARK: 1.0,
            },
            half_life_days=10_000.0,
        )
        default = price_of(
            observation(98.60, ObservationKind.EXECUTABLE_QUOTE, "Dealer A"),
            observation(95.00, ObservationKind.REPORTED_MARK, "Fund A", QUARTER_END),
        )
        theirs = price_of(
            observation(98.60, ObservationKind.EXECUTABLE_QUOTE, "Dealer A"),
            observation(95.00, ObservationKind.REPORTED_MARK, "Fund A", QUARTER_END),
            weighting=marks_matter_more,
        )
        assert theirs.price < default.price
        assert theirs.weighting_hash != default.weighting_hash


class TestTheScore:
    def test_abundant_firm_corroborated_evidence_scores_high(self) -> None:
        result = price_of(
            observation(98.50, source="A", size=5e6),
            observation(98.55, source="B", size=3e6),
            observation(98.48, source="C", size=2e6),
        )
        assert result.score >= 9
        assert result.level is FairValueLevel.LEVEL_2

    def test_one_stale_indication_scores_low(self) -> None:
        result = price_of(
            observation(98.50, ObservationKind.INDICATIVE_QUOTE, "A", date(2026, 7, 20))
        )
        assert result.score <= 4
        assert result.level is FairValueLevel.LEVEL_3

    def test_a_single_source_cannot_corroborate_itself(self) -> None:
        """One observation is an assertion. Scoring it as perfect agreement
        would let a single number look like a consensus."""
        result = price_of(observation(98.50, source="A", size=5e6))
        assert result.components.corroboration == 0.0
        assert result.components.agreement == 0.5

    def test_two_sources_agreeing_beat_one(self) -> None:
        alone = price_of(observation(98.50, source="A"))
        together = price_of(observation(98.50, source="A"), observation(98.51, source="B"))
        assert together.score > alone.score

    def test_the_same_source_twice_is_not_corroboration(self) -> None:
        """Attribution exists so that two observations from one desk cannot
        be counted as two opinions."""
        twice = price_of(observation(98.50, source="A"), observation(98.51, source="A"))
        assert twice.components.corroboration == 0.0

    def test_disagreement_lowers_the_score(self) -> None:
        agreeing = price_of(observation(98.50, source="A"), observation(98.52, source="B"))
        scattered = price_of(observation(95.00, source="A"), observation(101.00, source="B"))
        assert scattered.components.agreement < agreeing.components.agreement
        assert scattered.score < agreeing.score

    def test_firmness_measures_the_share_of_weight_not_the_count(self) -> None:
        """Two stale marks beside one live firm quote is a firm price, not a
        two-thirds-soft one."""
        result = price_of(
            observation(98.60, ObservationKind.EXECUTABLE_QUOTE, "Dealer A", size=5e6),
            observation(98.50, ObservationKind.REPORTED_MARK, "Fund A", QUARTER_END),
            observation(98.52, ObservationKind.REPORTED_MARK, "Fund B", QUARTER_END),
        )
        assert result.components.firmness > 0.9

    def test_every_component_is_published(self) -> None:
        """'Why is this a 4' is the question a valuation committee asks."""
        components = price_of(observation(98.50)).components
        for value in (
            components.corroboration,
            components.timeliness,
            components.firmness,
            components.agreement,
        ):
            assert 0.0 <= value <= 1.0

    def test_the_score_stays_in_range(self) -> None:
        for result in (
            price_of(observation(98.50, ObservationKind.INDICATIVE_QUOTE, "A", date(2026, 1, 5))),
            price_of(*(observation(98.5, source=f"S{i}", size=9e9) for i in range(20))),
        ):
            assert 1 <= result.score <= 10


class TestTheFairValueLevel:
    def test_stale_marks_are_level_3_however_well_they_agree(self) -> None:
        """Three funds agreeing to the cent at a quarter end is real
        corroboration and still says little about today. Past the freshness
        floor the price rests on the assumption that nothing has changed,
        and an assumption is what Level 3 means."""
        result = price_of(
            observation(98.50, ObservationKind.REPORTED_MARK, "F1", QUARTER_END),
            observation(98.50, ObservationKind.REPORTED_MARK, "F2", QUARTER_END),
            observation(98.51, ObservationKind.REPORTED_MARK, "F3", QUARTER_END),
        )
        assert result.components.agreement == pytest.approx(1.0)
        assert result.level is FairValueLevel.LEVEL_3

    def test_recent_marks_can_reach_level_2(self) -> None:
        """The rule is about staleness, not about marks being second class —
        otherwise it would be a prejudice rather than a policy."""
        recent = date(2026, 7, 31)
        result = price_of(
            observation(98.50, ObservationKind.REPORTED_MARK, "F1", recent),
            observation(98.50, ObservationKind.REPORTED_MARK, "F2", recent),
            observation(98.51, ObservationKind.REPORTED_MARK, "F3", recent),
        )
        assert result.level is FairValueLevel.LEVEL_2

    def test_the_mapping_is_a_published_parameter(self) -> None:
        """§15.2 requires the score-to-level mapping to be documented. Here
        it is also tunable, because an auditor may hold a different line."""
        strict = Weighting(level_2_min_score=10)
        result = price_of(
            observation(98.50, source="A", size=5e6),
            observation(98.51, source="B", size=5e6),
            weighting=strict,
        )
        assert result.score < 10
        assert result.level is FairValueLevel.LEVEL_3

    def test_level_1_is_not_produced(self) -> None:
        """It means an unadjusted quoted price in an active market for the
        identical asset — if one existed there would be nothing to
        evaluate. Absent from the enum rather than an unreachable branch."""
        assert {level.name for level in FairValueLevel} == {"LEVEL_2", "LEVEL_3"}


class TestTheDrillDown:
    def test_every_observation_comes_back_with_its_weight(self) -> None:
        """§15.3: a price that cannot be explained is not defensible."""
        result = price_of(
            observation(98.50, source="A", size=5e6),
            observation(98.55, source="B"),
        )
        assert len(result.observations) == 2
        for weighted in result.observations:
            assert weighted.weight == pytest.approx(
                weighted.kind_weight * weighted.recency_weight * weighted.size_weight
            )

    def test_contributions_sum_to_one(self) -> None:
        result = price_of(
            observation(98.50, source="A"),
            observation(98.55, source="B"),
            observation(98.60, source="C"),
        )
        assert sum(w.contribution for w in result.observations) == pytest.approx(1.0)

    def test_the_price_is_reproducible_from_the_drill_down(self) -> None:
        """The strongest form of transparency: a reader can recompute the
        headline number from the rows shown beneath it."""
        result = price_of(
            observation(98.50, source="A", size=5e6),
            observation(99.00, ObservationKind.INDICATIVE_QUOTE, "B"),
            observation(97.00, ObservationKind.REPORTED_MARK, "F1", date(2026, 7, 30)),
        )
        rebuilt = sum(w.contribution * w.observation.price for w in result.observations)
        assert rebuilt == pytest.approx(result.price)

    def test_observations_are_ordered_by_influence(self) -> None:
        result = price_of(
            observation(98.50, ObservationKind.REPORTED_MARK, "F1", date(2026, 7, 30)),
            observation(98.60, ObservationKind.EXECUTABLE_QUOTE, "A", size=5e6),
        )
        contributions = [w.contribution for w in result.observations]
        assert contributions == sorted(contributions, reverse=True)

    def test_the_weighting_used_is_stamped_on_the_result(self) -> None:
        """Two prices computed under different weights must not be
        indistinguishable after the fact."""
        assert price_of(observation(98.50)).weighting_hash == Weighting().content_hash


class TestRefusals:
    def test_no_observations_is_refused(self) -> None:
        """TVAL's contract is a price *and* how much to trust it. A price
        with nothing behind it has no honest score, and scoring it 1 would
        still put an unsupported number on a screen."""
        with pytest.raises(UnpriceableError, match="no observations"):
            evaluate_price((), as_of=AS_OF)

    def test_only_stale_observations_is_refused(self) -> None:
        with pytest.raises(UnpriceableError, match="older than"):
            price_of(observation(98.50, observed_at=date(2020, 1, 1)))

    def test_the_count_of_dropped_observations_is_reported(self) -> None:
        """A reader must be able to tell 'no evidence' from 'no *recent*
        evidence'."""
        result = price_of(
            observation(98.50, source="A"),
            observation(50.00, source="B", observed_at=date(2020, 1, 1)),
        )
        assert result.dropped_stale == 1
        assert len(result.observations) == 1

    def test_an_observation_from_the_future_is_refused(self) -> None:
        with pytest.raises(UnpriceableError, match="after the valuation date"):
            price_of(observation(98.50, observed_at=date(2027, 1, 1)))

    def test_a_weighting_that_values_nothing_is_refused(self) -> None:
        """Falling back to an unweighted average would be using weights the
        caller explicitly set to zero."""
        nothing_counts = Weighting(kind_weight={})
        with pytest.raises(UnpriceableError, match="weighted to zero"):
            price_of(observation(98.50), weighting=nothing_counts)

    def test_a_non_positive_price_is_not_an_observation(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            observation(0.0)


class TestModelIdentity:
    def test_the_result_is_enveloped(self) -> None:
        result = evaluate_price((observation(98.50),), as_of=AS_OF)
        assert isinstance(result, ModelResult)
        assert result.model_id == "tval.evaluate_price"

    def test_the_envelope_records_the_weighting(self) -> None:
        """I4: the weights are an input with a content hash, so the envelope
        pins which published weighting produced the number."""
        weighting = Weighting(half_life_days=9.0)
        result = evaluate_price((observation(98.50),), as_of=AS_OF, weighting=weighting)
        assert result.inputs["weighting"] == weighting.content_hash
