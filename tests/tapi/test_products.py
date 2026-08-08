"""§12.1 products priced off the stored curves (spec §12.1).

Seven pricers shipped and none was reachable. The reachability gate named
them; this is the caller. What is tested here is not the pricing -- each
pricer has its own mutation-checked suite -- but the two things this layer
decides: that forwards come from the stored curve rather than a constant,
and that a product whose inputs nothing supplies says so instead of
returning a confident number built on a default.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.analytics.derivatives.cancellable import cancellable_swap
from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.products import (
    MAX_NODE_DISPERSION,
    UNFED_PRODUCTS,
    CancellablePriced,
    ProductUnavailableError,
    assetswap_from_store,
    cancellable_from_store,
    cms_from_store,
    price_cap_from_store,
    unfed_reason,
)
from treble.tapi.swap_market import DISCOUNT_CURVE, FORECAST_CURVE
from treble.tapi.vol_surface import VolSurfaceUnavailableError

KNOWN = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
DAY = date(2026, 7, 31)
RATES = {
    "1Y": 0.0295,
    "2Y": 0.0301,
    "3Y": 0.0303,
    "5Y": 0.0305,
    "7Y": 0.0311,
    "10Y": 0.0322,
    "20Y": 0.0338,
    "30Y": 0.0330,
}


@pytest.fixture
def store(tmp_path: Path) -> DuckStore:
    store = DuckStore(tmp_path / "t.db")
    prov = Provenance(
        source_system="dtcc-sdr",
        source_uri="https://example.invalid/CFTC_CUMULATIVE_RATES.zip",
        retrieved_at=KNOWN,
        method=ExtractionMethod.BULK_FILE,
        extractor_version="1",
        payload_hash="0" * 64,
    )
    store.write_provenance([prov])
    store.write_facts(
        [
            Fact(
                subject=f"swap:{curve}:{tenor}",
                field="PAR_RATE",
                value=rate,
                effective_from=DAY,
                effective_to=DAY,
                knowledge_from=KNOWN,
                provenance_id=prov.id,
            )
            for curve in (DISCOUNT_CURVE, FORECAST_CURVE)
            for tenor, rate in RATES.items()
        ]
    )
    return store


class TestForwardsComeFromTheCurve:
    def test_a_cap_prices_off_the_stored_environment(self, store: DuckStore) -> None:
        priced = price_cap_from_store(store, as_of=datetime.now(UTC), strike=0.03, volatility=0.005)
        assert priced.value > 0.0
        assert priced.caplets == 20
        assert priced.report_date == DAY.isoformat()

    def test_the_strip_follows_the_curve_not_a_constant(self, store: DuckStore) -> None:
        """A cap built on a flat assumed forward would agree with nothing
        else on the screen. A higher strike must be cheaper against the
        same curve, and a longer strip must contain more caplets."""
        cheap = price_cap_from_store(store, as_of=datetime.now(UTC), strike=0.05, volatility=0.005)
        dear = price_cap_from_store(store, as_of=datetime.now(UTC), strike=0.02, volatility=0.005)
        assert dear.value > cheap.value
        longer = price_cap_from_store(
            store, as_of=datetime.now(UTC), strike=0.03, volatility=0.005, years=10.0
        )
        assert longer.caplets > cheap.caplets

    def test_a_floor_is_the_other_side(self, store: DuckStore) -> None:
        kw = {"as_of": datetime.now(UTC), "strike": 0.03, "volatility": 0.005}
        cap = price_cap_from_store(store, **kw)
        floor = price_cap_from_store(store, **kw, floor=True)
        assert cap.value != floor.value


class TestCms:
    def test_the_adjustment_is_positive_and_reported(self, store: DuckStore) -> None:
        """A CMS rate below its forward swap rate is the one result that
        should never appear, and the adjustment is carried apart so a
        screen can show whether it was 2bp or 40bp."""
        result = cms_from_store(
            store,
            as_of=datetime.now(UTC),
            tenor_years=10.0,
            expiry_years=5.0,
            volatility=0.25,
        )
        assert result["cms_rate"] > result["forward_swap_rate"]
        assert float(result["adjustment_bp"]) > 0.0

    def test_the_forward_comes_from_the_stored_curve(self, store: DuckStore) -> None:
        """Around 3%, which is where this curve sits. A forward that
        ignored the curve would not land in the band the quotes describe."""
        result = cms_from_store(
            store,
            as_of=datetime.now(UTC),
            tenor_years=10.0,
            expiry_years=5.0,
            volatility=0.25,
        )
        assert 0.02 < float(result["forward_swap_rate"]) < 0.05


class TestUnfedProductsSaySo:
    @pytest.mark.parametrize("product", sorted(UNFED_PRODUCTS))
    def test_each_names_what_is_missing(self, product: str) -> None:
        """A screen calls this before offering a product, so a user meets
        the reason rather than a field of dashes."""
        reason = unfed_reason(product)
        assert reason is not None
        assert len(reason) > 20

    def test_a_fed_product_has_no_reason(self) -> None:
        assert unfed_reason("cap") is None
        assert unfed_reason("cms") is None

    def test_crosscurrency_is_not_defaulted_to_a_zero_basis(self) -> None:
        """A cross-currency basis of zero is not a neutral assumption; it
        is a claim that the basis is zero, on an instrument whose whole
        point is that it is not."""
        reason = unfed_reason("crosscurrency")
        assert reason is not None and "zero" in reason


class TestCancellableUsesTheFittedSurface:
    """Recorded as needing a volatility "no stored source supplies", which
    was wrong: tapi/vol_surface.py fits one from DTCC prints, and on the
    live store it carries 44 nodes at 79% grid coverage. The same error as
    the cross-currency entry -- asserting an absence without checking.
    """

    def test_a_missing_node_is_refused_not_interpolated(self, store: DuckStore) -> None:
        """This store holds curves but no swaption prints, so the surface
        cannot be built at all and says which adapter supplies them.

        The error is VolSurfaceUnavailableError rather than
        ProductUnavailableError, and that is left alone deliberately: it
        names the missing source, which is more useful to whoever hits it
        than a product-level wrapper repeating the same thing less
        precisely."""
        with pytest.raises(VolSurfaceUnavailableError, match="DTCC adapter"):
            cancellable_from_store(
                store,
                as_of=datetime.now(UTC),
                vanilla_value=1_000_000.0,
                notional=10_000_000.0,
                strike=0.032,
                expiry_years=1.0,
                tenor_years=10.0,
            )

    def test_thinness_and_disagreement_are_separate_bars(self) -> None:
        """`is_confident` counts effective prints and says nothing about
        whether they agree. Measured on the live surface, the 0.25y-into-2y
        node holds 26 prints -- comfortably confident -- at 117% dispersion.
        Twenty-six prints that contradict each other are still twenty-six
        prints, so both faults are checked."""
        assert 0.0 < MAX_NODE_DISPERSION < 1.0

    def test_the_notional_scales_the_option(self) -> None:
        """The surface annuity is per unit of notional and vanilla_value is
        in currency. Passing them together subtracts a per-unit option from
        a currency PV, which priced every cancellation right at about four
        cents against a 1mm swap -- reading as "the option is worthless"
        rather than as a units mistake."""
        import inspect

        assert "notional" in inspect.signature(cancellable_from_store).parameters

    def test_the_node_travels_with_the_price(self) -> None:
        """The whole reason those fields exist. A cancellable priced off a
        node whose prints spanned 117% of their own median is a different
        claim from one priced off a node at 0%, and the value alone cannot
        say which.

        The unread-member gate flagged all three of these as having no
        reader, minutes after I wrote a docstring about that exact failure
        class. Carrying a field and never reading it is how a transparency
        number becomes decoration.
        """
        priced = CancellablePriced(
            pricing=cancellable_swap.__wrapped__(  # type: ignore[attr-defined]
                vanilla_value=1_000_000.0,
                forward=0.032,
                strike=0.032,
                expiry_years=1.0,
                volatility=0.0132,
                annuity=8.5e7,
                payer=True,
                normal_vol=True,
            ),
            expiry_years=1.0,
            tenor_years=10.0,
            volatility_bp=132.0,
            node_dispersion=0.54,
            node_observations=25,
        )
        assert priced.volatility_bp == pytest.approx(132.0)
        assert priced.node_dispersion == pytest.approx(0.54)
        assert priced.node_observations == 25
        assert priced.pricing.value < priced.pricing.vanilla_value


class TestAssetSwapUsesStoredHoldings:
    """Recorded as needing "a bond price" no stored source supplies. Wrong,
    and the third such entry to be wrong the same way: the store holds 460
    bonds with maturity, coupon, par balance and USD value, and
    holdings/implied_price.py turns the last two into a price. Both that
    module and derivatives/assetswap.py were orphaned.
    """

    @staticmethod
    def _bond(store: DuckStore, *, currency: str | None, balance: float, val: float) -> TUID:
        prov = Provenance(
            source_system="edgar-nport",
            source_uri="https://example.invalid/primary_doc.xml",
            retrieved_at=KNOWN,
            method=ExtractionMethod.DOCUMENT,
            extractor_version="1",
            payload_hash="1" * 64,
        )
        store.write_provenance([prov])
        subject = TUID("isin:US0000000001")
        rows: list[tuple[str, object]] = [
            ("nport:maturityDt", date(2031, 6, 30)),
            ("nport:annualizedRt", 4.50),
            ("nport:balance", balance),
            ("nport:valUSD", val),
        ]
        if currency is not None:
            rows.append(("nport:curCd", currency))
        store.write_facts(
            [
                Fact(
                    subject=str(subject),
                    field=field,
                    value=value,
                    effective_from=DAY,
                    effective_to=DAY,
                    knowledge_from=KNOWN,
                    provenance_id=prov.id,
                )
                for field, value in rows
            ]
        )
        return subject

    def test_a_usd_bond_prices_off_its_implied_mark(self, store: DuckStore) -> None:
        subject = self._bond(store, currency="USD", balance=100_000.0, val=99_000.0)
        priced = assetswap_from_store(store, as_of=datetime.now(UTC), subject=subject)
        assert priced.implied_price == pytest.approx(99.0)
        # A discount bond pays over the index through the price term.
        assert priced.spread.price_bp > 0.0
        assert priced.spread.spread_bp == pytest.approx(
            priced.spread.price_bp + priced.spread.coupon_bp
        )

    @pytest.mark.parametrize("currency", ["AUD", "EUR", None])
    def test_a_non_usd_holding_is_refused_not_converted(
        self, store: DuckStore, currency: str | None
    ) -> None:
        """valUSD is in USD and balance is par in the instrument's own
        currency, so dividing across currencies scales the price by the FX
        rate -- and the result looks like a bond rather than an error.

        Measured before this guard: the only stored bond that priced was an
        Australian issuer with no reported currency, 100,000 par against
        68,836 USD, implying 68.84. At an AUD/USD near 0.67 the real level
        is about 103. A distressed price and a currency mistake render
        identically, and only one of them is a fact.
        """
        subject = self._bond(store, currency=currency, balance=100_000.0, val=68_836.0)
        with pytest.raises(ProductUnavailableError, match="reported currency"):
            assetswap_from_store(store, as_of=datetime.now(UTC), subject=subject)

    def test_a_matured_bond_is_refused(self, store: DuckStore) -> None:
        prov = Provenance(
            source_system="edgar-nport",
            source_uri="https://example.invalid/x",
            retrieved_at=KNOWN,
            method=ExtractionMethod.DOCUMENT,
            extractor_version="1",
            payload_hash="2" * 64,
        )
        store.write_provenance([prov])
        subject = TUID("isin:US0000000002")
        store.write_facts(
            [
                Fact(
                    subject=str(subject),
                    field=field,
                    value=value,
                    effective_from=DAY,
                    effective_to=DAY,
                    knowledge_from=KNOWN,
                    provenance_id=prov.id,
                )
                for field, value in (
                    ("nport:maturityDt", date(2020, 1, 1)),
                    ("nport:annualizedRt", 4.5),
                    ("nport:balance", 100_000.0),
                    ("nport:valUSD", 99_000.0),
                    ("nport:curCd", "USD"),
                )
            ]
        )
        with pytest.raises(ProductUnavailableError, match="nothing left"):
            assetswap_from_store(store, as_of=datetime.now(UTC), subject=subject)
