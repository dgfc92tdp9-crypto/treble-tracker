"""I4 kill-tests: CurveConfig is frozen and content-addressed.

The pinned golden hash catches accidental serialisation drift — if the hash
of this exact config ever changes, previously stamped results (I3 envelopes)
would no longer be reproducible, which is precisely what I4 forbids.
"""

import pytest
from pydantic import ValidationError

from treble.analytics._ql import DayCount, Market
from treble.analytics.curves.config import (
    CurveConfig,
    InstrumentKind,
    InstrumentSpec,
    Interpolation,
)

REFERENCE_CONFIG = CurveConfig(
    name="USD-SOFR-OIS",
    currency="USD",
    instruments=(
        InstrumentSpec(kind=InstrumentKind.DEPOSIT, tenor="1W"),
        InstrumentSpec(kind=InstrumentKind.OIS, tenor="1Y"),
        InstrumentSpec(kind=InstrumentKind.OIS, tenor="5Y"),
        InstrumentSpec(kind=InstrumentKind.OIS, tenor="10Y"),
    ),
    interpolation=Interpolation.MONOTONE_CONVEX,
    day_count=DayCount.ACT_365F,
    calendar=Market.US_GOVERNMENT,
    settlement_days=2,
)

# Pinned. If this assertion ever fails, the serialisation changed and every
# stamped envelope hash in existence is invalidated — that is a breaking
# change requiring a decision record, not a test update.
#
# Re-pinned once, on 2026-08-01, for ADR-0006: `index_tenor` and the swap
# legs' day counts and frequency joined the config so that a forecast curve's
# identity does not rest on its display name. The previous value was
# af6a2d6d50a23b145059314a1bf480719c488c801ec48310d3afc912eaffa227. This test
# is the reason that change could not be made quietly, which is its job.
PINNED_HASH = "0f5b8cdc8fe6bc817904e681a3ed73458fc1b2931559016d788f83a487316fe6"


class TestContentHash:
    def test_hash_is_stable_golden(self) -> None:
        assert REFERENCE_CONFIG.content_hash == PINNED_HASH

    def test_the_new_identity_fields_change_the_hash(self) -> None:
        """ADR-0006: the fields were added *because* they change identity.

        A curve forecasting a 3M index and one forecasting 6M can otherwise
        be identical in every respect that the hash sees, and would then be
        indistinguishable in an I3 envelope.
        """
        for change in (
            {"index_tenor": "3M"},
            {"fixed_leg_day_count": DayCount.THIRTY_360},
            {"float_leg_day_count": DayCount.ACT_360},
            {"swap_fixed_frequency": 2},
        ):
            altered = REFERENCE_CONFIG.model_copy(update=change)
            assert altered.content_hash != REFERENCE_CONFIG.content_hash, change

    def test_hash_deterministic_across_instances(self) -> None:
        clone = REFERENCE_CONFIG.model_copy(deep=True)
        assert clone.content_hash == REFERENCE_CONFIG.content_hash

    def test_hash_changes_with_any_field(self) -> None:
        for change in (
            {"interpolation": Interpolation.LINEAR_ZERO},
            {"settlement_days": 1},
            {"name": "USD-SOFR-OIS-ALT"},
        ):
            altered = REFERENCE_CONFIG.model_copy(update=change)
            assert altered.content_hash != REFERENCE_CONFIG.content_hash


class TestImmutability:
    def test_config_is_frozen(self) -> None:
        with pytest.raises(ValidationError):
            REFERENCE_CONFIG.name = "changed"  # type: ignore[misc]

    def test_instrument_spec_is_frozen(self) -> None:
        spec = REFERENCE_CONFIG.instruments[0]
        with pytest.raises(ValidationError):
            spec.tenor = "2W"  # type: ignore[misc]


class TestValidation:
    def test_rejects_empty_instruments(self) -> None:
        with pytest.raises(ValidationError):
            CurveConfig(name="X", currency="USD", instruments=())

    def test_rejects_bad_tenor(self) -> None:
        with pytest.raises(ValidationError):
            InstrumentSpec(kind=InstrumentKind.OIS, tenor="10Q")

    def test_rejects_bad_currency(self) -> None:
        with pytest.raises(ValidationError):
            CurveConfig(
                name="X",
                currency="usd",
                instruments=(InstrumentSpec(kind=InstrumentKind.OIS, tenor="1Y"),),
            )
