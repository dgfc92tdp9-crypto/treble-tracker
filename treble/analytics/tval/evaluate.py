"""`TVAL` — evaluated pricing and the TVAL Score (spec §15).

    For the roughly one million fixed income securities that do not trade
    on a given day, someone must produce a price. Funds must strike a NAV
    daily regardless.

This is the component where methodology matters more than the number. A
wrong price with a low score and a visible derivation is a usable input to a
valuation committee. A right price nobody can explain is not, and a wrong
price presented confidently is the thing this whole system exists to refuse.

**The weighting function is published, not proprietary** (§15.1). It is
:class:`Weighting` — an ordinary frozen model with a content hash, passed in
rather than baked in, so a user can run the same machinery with their own
weights and get their own valuation (§15.3). Hard-coding it would make
"independent price verification" impossible by construction.

**What ships here is Prong 1 only.** Direct observations, weighted by
recency, firmness, size and corroboration. Prong 2 — the relative value
algorithm that prices a bond from comparables when it has no activity of its
own — needs an issuer curve fitted across the issuer's outstanding debt and
a similarity metric over sector, rating and seniority. That is not built,
and a price derived from nothing would score 1 rather than silently become a
model output dressed as an observation.

**On the observations actually available here.** TRACE is subscription-gated
and forbids automated access, so there are no corporate bond *trades*. What
exists is N-PORT: funds disclosing what they held and what they valued it
at. Those are **reported marks** — someone else's evaluation as of a
quarter end — not prints. Three filers independently marking the same bond
to the same cent is real corroboration, and it is still not a trade. The
distinction is in :class:`ObservationKind` rather than in a comment, because
a mark treated as a trade would score far higher than it earns.
"""

from __future__ import annotations

import enum
import hashlib
import json
import math
import statistics
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from treble.analytics.registry import model


class ObservationKind(enum.Enum):
    """What kind of evidence a price observation is.

    Ordered by how much it says about where the bond would trade now. The
    distinction drives the weight *and* the score, so collapsing two kinds
    into one would inflate confidence rather than merely change a number.
    """

    #: A trade. The strongest evidence: someone paid this.
    TRADE = "trade"
    #: A firm two-way price from the contributed network — someone will
    #: trade at it now.
    EXECUTABLE_QUOTE = "executable-quote"
    #: A dealer's indication. An opinion, not a commitment.
    INDICATIVE_QUOTE = "indicative-quote"
    #: A holder's own valuation, disclosed after the fact — N-PORT. Real
    #: evidence, quarterly, and not a market price.
    REPORTED_MARK = "reported-mark"


class FairValueLevel(enum.Enum):
    """ASC 820 / IFRS 13 fair value hierarchy (§15.2).

    **Level 1 is deliberately absent.** It means an unadjusted quoted price
    in an active market for the identical asset, which no evaluated price
    can be — if a Level 1 price existed there would be nothing for TVAL to
    evaluate. Including it as an unreachable branch would be a
    classification the code claims to produce and never can.
    """

    LEVEL_2 = 2
    LEVEL_3 = 3


class PriceObservation(BaseModel):
    """One piece of evidence about a security's price."""

    model_config = ConfigDict(frozen=True)

    price: float = Field(gt=0.0)
    kind: ObservationKind
    #: Who: a contributor, a filer, a venue. Required — an observation that
    #: cannot be attributed cannot be corroborated, since two anonymous
    #: observations might be one source counted twice.
    source: str
    observed_at: date
    #: Notional behind the observation, when known. `None` is not zero: a
    #: level published without size is weaker evidence, not evidence of a
    #: trade in nothing.
    size: float | None = Field(default=None, gt=0.0)


class Weighting(BaseModel):
    """The published weighting function (§15.1).

    Every number a user would need to reproduce or dispute a TVAL price.
    Passed to :func:`evaluate_price` rather than referenced from inside it,
    so running the algorithm with different weights is a parameter change
    and not a fork.
    """

    model_config = ConfigDict(frozen=True)

    #: Weight per kind of evidence, before recency and size.
    kind_weight: dict[ObservationKind, float] = Field(
        default_factory=lambda: {
            ObservationKind.TRADE: 1.0,
            ObservationKind.EXECUTABLE_QUOTE: 0.8,
            ObservationKind.INDICATIVE_QUOTE: 0.4,
            # A quarter-end mark is real evidence about a quarter end, and
            # weak evidence about today. The decay below does most of the
            # work; this is the standing discount for "someone's valuation,
            # not a market price".
            ObservationKind.REPORTED_MARK: 0.3,
        }
    )
    #: Days over which an observation loses half its weight.
    half_life_days: float = Field(default=5.0, gt=0.0)
    #: Beyond this an observation is not evidence about today's price and is
    #: dropped rather than decayed to near-nothing — a hundred ancient marks
    #: would otherwise out-vote one fresh quote on sheer count.
    max_age_days: int = Field(default=400, gt=0)
    #: Size scaling. A larger observation is better evidence, with
    #: diminishing returns; `None` size gets `size_floor`.
    size_reference: float = Field(default=1_000_000.0, gt=0.0)
    size_floor: float = Field(default=0.5, gt=0.0, le=1.0)
    #: Score at or above which a price may be Level 2 (§15.2 requires the
    #: score-to-level mapping to be documented; here it is also tunable,
    #: because a fund's auditor may hold a different line than this default).
    level_2_min_score: int = Field(default=5, ge=1, le=10)
    #: Freshness floor for Level 2, as a recency weight in [0, 1].
    #:
    #: ASC 820 Level 2 permits observable inputs; the question this answers
    #: is when an input is too *old* to be observable evidence about today.
    #: Past this point the price rests mainly on the assumption that nothing
    #: has changed since the observation, and an assumption is exactly what
    #: Level 3 means. 0.25 is two half-lives. Without this floor a set of
    #: quarter-end N-PORT marks scored Level 2 at a timeliness of 0.01,
    #: which would let a stale mark carry a classification a fund reports to
    #: its auditors.
    level_2_min_timeliness: float = Field(default=0.25, ge=0.0, le=1.0)

    @property
    def content_hash(self) -> str:
        """Content address, so an envelope pins the weights used (I4)."""
        payload = {
            "kind_weight": {k.value: v for k, v in sorted(self.kind_weight.items(), key=str)},
            "half_life_days": self.half_life_days,
            "max_age_days": self.max_age_days,
            "size_reference": self.size_reference,
            "size_floor": self.size_floor,
            "level_2_min_score": self.level_2_min_score,
            "level_2_min_timeliness": self.level_2_min_timeliness,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class WeightedObservation(BaseModel):
    """An observation and exactly why it counted for what it did (§15.3)."""

    model_config = ConfigDict(frozen=True)

    observation: PriceObservation
    age_days: int
    kind_weight: float
    recency_weight: float
    size_weight: float
    weight: float
    #: Share of the final price this observation is responsible for.
    contribution: float


class ScoreComponents(BaseModel):
    """The four drivers of the TVAL Score, each in [0, 1] (§15.2).

    Published separately rather than rolled into one number, because "why
    is this a 4" is the question a valuation committee actually asks.
    """

    model_config = ConfigDict(frozen=True)

    #: Distinct attributable sources — one source is an assertion, several
    #: agreeing is evidence.
    corroboration: float
    #: How fresh the best evidence is.
    timeliness: float
    #: How much of the weight comes from trades and firm quotes rather than
    #: indications and marks.
    firmness: float
    #: How closely the observations agree with each other.
    agreement: float


class TvalPrice(BaseModel):
    """An evaluated price, its confidence, and its full derivation."""

    model_config = ConfigDict(frozen=True)

    price: float
    #: 1 to 10. 9 to 10 means abundant corroborating direct observations; a low
    #: score means the price rests on thin or stale evidence.
    score: int
    level: FairValueLevel
    components: ScoreComponents
    #: Every observation that counted, with its weight — the drill-down.
    observations: tuple[WeightedObservation, ...]
    #: Observations dropped for age, so a reader can tell "no evidence"
    #: from "no *recent* evidence".
    dropped_stale: int
    weighting_hash: str


class UnpriceableError(ValueError):
    """There is nothing to price from.

    Raised rather than returning a modelled guess. TVAL's contract is a
    price *and* how much to trust it; a price with no observations behind it
    has no honest score, and scoring it 1 would still put a number on a
    screen that nothing supports.
    """


def _size_weight(size: float | None, weighting: Weighting) -> float:
    """Diminishing returns in size, floored when size is unknown."""
    if size is None:
        return weighting.size_floor
    # sqrt so a ten-times-larger trade is about three times the evidence,
    # not ten times. Capped at 1.0: past the reference size, more size does
    # not make the price more true.
    return min(1.0, max(weighting.size_floor, math.sqrt(size / weighting.size_reference)))


def _agreement(prices: list[float], price: float) -> float:
    """1 when the observations coincide, falling as they scatter."""
    if len(prices) < 2 or price <= 0.0:
        # A single observation cannot corroborate itself. Scoring it as
        # perfect agreement would let one number look like a consensus, so
        # it scores neutral and `corroboration` carries the penalty.
        return 0.5
    spread = statistics.pstdev(prices)
    # 50bp of price is treated as full agreement, 5% as none.
    relative = spread / price
    return max(0.0, min(1.0, 1.0 - (relative - 0.005) / 0.045))


@model(
    model_id="tval.evaluate_price",
    version="1.0",
    spec_section="§15",
    summary="Evaluated price from direct observations, with TVAL Score and derivation",
)
def evaluate_price(
    observations: tuple[PriceObservation, ...],
    *,
    as_of: date,
    weighting: Weighting | None = None,
) -> TvalPrice:
    """Weight the direct observations and score the result.

    Prong 1 of §15.1. Every observation's weight is the product of its kind
    weight, an exponential recency decay, and a size factor; the price is
    the weighted mean. The whole derivation comes back in the result rather
    than being recomputed for display, so what a screen shows is what the
    number was actually made of.
    """
    weights_used = weighting or Weighting()
    if not observations:
        raise UnpriceableError(
            "no observations: TVAL returns a price and a confidence in it, and a price "
            "with nothing behind it has no honest confidence. Prong 2 (comparables) is "
            "not built, so there is no modelled fallback either"
        )

    fresh: list[tuple[PriceObservation, int]] = []
    dropped = 0
    for observation in observations:
        age = (as_of - observation.observed_at).days
        if age < 0:
            raise UnpriceableError(
                f"observation from {observation.source} is dated {observation.observed_at}, "
                f"after the valuation date {as_of}: a price cannot be evidence about a "
                "moment before it existed"
            )
        if age > weights_used.max_age_days:
            dropped += 1
            continue
        fresh.append((observation, age))

    if not fresh:
        raise UnpriceableError(
            f"all {dropped} observation(s) are older than "
            f"{weights_used.max_age_days} days; stale evidence is not evidence about "
            "today, and averaging it would produce a price with a date nobody could name"
        )

    weighted: list[WeightedObservation] = []
    for observation, age in fresh:
        kind_weight = weights_used.kind_weight.get(observation.kind, 0.0)
        recency = 0.5 ** (age / weights_used.half_life_days)
        size = _size_weight(observation.size, weights_used)
        weighted.append(
            WeightedObservation(
                observation=observation,
                age_days=age,
                kind_weight=kind_weight,
                recency_weight=recency,
                size_weight=size,
                weight=kind_weight * recency * size,
                contribution=0.0,
            )
        )

    total = sum(w.weight for w in weighted)
    if total <= 0.0:
        raise UnpriceableError(
            "every observation weighted to zero, which means the weighting function "
            "assigns no value to any kind of evidence supplied. Returning their unweighted "
            "average would be using weights the caller explicitly set to nothing"
        )

    price = sum(w.weight * w.observation.price for w in weighted) / total
    weighted = [w.model_copy(update={"contribution": w.weight / total}) for w in weighted]
    weighted.sort(key=lambda w: w.contribution, reverse=True)

    sources = {w.observation.source for w in weighted}
    firm_kinds = (ObservationKind.TRADE, ObservationKind.EXECUTABLE_QUOTE)
    components = ScoreComponents(
        corroboration=min(1.0, (len(sources) - 1) / 3.0),
        timeliness=max(w.recency_weight for w in weighted),
        firmness=sum(w.contribution for w in weighted if w.observation.kind in firm_kinds),
        agreement=_agreement([w.observation.price for w in weighted], price),
    )
    score = _score(components)
    # Level 2 needs observable inputs that are still observable *about
    # today*. Both conditions are published parameters rather than constants
    # here, because §15.2 requires the score-to-level mapping to be
    # documented and a fund's auditor may hold a different line.
    is_level_2 = (
        score >= weights_used.level_2_min_score
        and components.timeliness >= weights_used.level_2_min_timeliness
    )
    return TvalPrice(
        price=price,
        score=score,
        level=FairValueLevel.LEVEL_2 if is_level_2 else FairValueLevel.LEVEL_3,
        components=components,
        observations=tuple(weighted),
        dropped_stale=dropped,
        weighting_hash=weights_used.content_hash,
    )


def _score(components: ScoreComponents) -> int:
    """Combine the four drivers into 1 to 10.

    Equal weights, deliberately: any other split is a claim about relative
    importance that nothing here measures, and an unjustified weighting
    dressed up in decimals is worse than a stated simplification.
    """
    average = (
        components.corroboration
        + components.timeliness
        + components.firmness
        + components.agreement
    ) / 4.0
    return max(1, min(10, round(1 + average * 9)))


__all__ = [
    "FairValueLevel",
    "ObservationKind",
    "PriceObservation",
    "ScoreComponents",
    "TvalPrice",
    "UnpriceableError",
    "WeightedObservation",
    "Weighting",
    "evaluate_price",
]
