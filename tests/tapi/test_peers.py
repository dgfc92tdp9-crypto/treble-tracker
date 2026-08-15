"""Peer relative value, for issuers with no curve of their own.

`TVAL`'s issuer curve needs three bonds from one entity. Twenty-eight of
153 issuers clear that, so 157 of 269 bonds were absent from the rich/cheap
ranking entirely — not refused, simply not there. `ComparableSet` was built
for exactly them and called by nothing outside its own suite.

The tests are about the two things that stop a weak method reading like a
strong one: the sign convention, and the noise gate.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from treble.core.facts import Fact
from treble.core.provenance import ExtractionMethod, Provenance
from treble.store.duck import DuckStore
from treble.tapi.peers import MIN_PEERS, NoPeersError, peer_values

KNOWN = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
DAY = date(2026, 3, 31)


def _store(
    tmp_path: Path, bonds: list[tuple[str, str, float, int]], *, name: str = "p"
) -> DuckStore:
    """`bonds` is (isin, lei, price, years to maturity). Coupon is 5%."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = DuckStore(tmp_path / f"{name}.db")
    record = Provenance(
        source_system="edgar-nport",
        source_uri="https://example.invalid/n",
        retrieved_at=KNOWN,
        method=ExtractionMethod.BULK_FILE,
        extractor_version="1",
        payload_hash="a" * 64,
    )
    store.write_provenance([record])
    facts = []
    for isin, lei, price, years in bonds:
        for field, value in {
            "gleif:lei": lei,
            "nport:assetCat": "DBT",
            "nport:issuerCat": "CORP",
            "nport:curCd": "USD",
            "nport:name": f"ISSUER {lei[-2:]}",
            "nport:maturityDt": date(DAY.year + years, DAY.month, DAY.day),
            "nport:annualizedRt": 5.0,
            "nport:valUSD": 1_000_000.0 * price / 100.0,
            "nport:balance": 1_000_000.0,
        }.items():
            facts.append(
                Fact(
                    subject=f"isin:{isin}",
                    field=field,
                    value=value,
                    effective_from=DAY,
                    effective_to=DAY,
                    knowledge_from=KNOWN,
                    provenance_id=record.id,
                )
            )
    store.write_facts(facts)
    return store


def _universe(cheap_price: float = 100.0) -> list[tuple[str, str, float, int]]:
    """Three bonds for issuer AA (fittable) plus singletons around par.

    The singleton `US0000000099` is the bond under test: its issuer has one
    line, so no curve can be fitted for it.
    """
    bonds = [(f"US000000000{i}", "AA", 100.0, 3 + i) for i in range(1, 4)]
    bonds += [(f"US00000000{10 + i}", f"S{i}", 100.0, 5) for i in range(1, 6)]
    bonds.append(("US0000000099", "SX", cheap_price, 5))
    return bonds


class TestItValuesBondsTheCurveCannot:
    def test_a_singleton_issuer_gets_a_call(self, tmp_path: Path) -> None:
        """The whole point: this bond's issuer has one line, so `TVAL`'s
        curve tab has nothing to say about it at all."""
        values = peer_values(_store(tmp_path, _universe()), as_of=LATER)
        assert any(v.identifier == "isin:US0000000099" for v in values)

    def test_fitted_issuers_are_excluded_by_default(self, tmp_path: Path) -> None:
        """A bond with an issuer curve has a better call available, and
        showing both without distinction would invite reading the weaker
        one as confirmation of the stronger."""
        values = peer_values(_store(tmp_path, _universe()), as_of=LATER)
        assert all(not v.identifier.endswith(("001", "002", "003")) for v in values)

    def test_the_whole_universe_can_be_valued_when_asked(self, tmp_path: Path) -> None:
        """Needed to check a peer call against a curve call on the bonds
        where both exist — the only external check available on a method
        with no traded prices behind it."""
        wide = peer_values(
            _store(tmp_path, _universe(), name="wide"), as_of=LATER, only_unfitted=False
        )
        narrow = peer_values(_store(tmp_path, _universe(), name="narrow"), as_of=LATER)
        assert len(wide) > len(narrow)


class TestTheSignConvention:
    def test_a_higher_yield_is_cheap(self, tmp_path: Path) -> None:
        """Positive residual means yielding more than the peer median,
        which is cheap. Inverting this would label every bargain rich and
        every rich bond a bargain, and nothing else in the table would
        contradict it."""
        values = peer_values(_store(tmp_path, _universe(cheap_price=90.0)), as_of=LATER)
        target = next(v for v in values if v.identifier == "isin:US0000000099")
        assert target.residual_bp > 0.0
        assert target.verdict in {"cheap", "in noise"}

    def test_a_lower_yield_is_rich(self, tmp_path: Path) -> None:
        values = peer_values(
            _store(tmp_path, _universe(cheap_price=110.0), name="rich"), as_of=LATER
        )
        target = next(v for v in values if v.identifier == "isin:US0000000099")
        assert target.residual_bp < 0.0
        assert target.verdict in {"rich", "in noise"}

    def test_a_bond_at_the_median_is_in_noise(self, tmp_path: Path) -> None:
        values = peer_values(_store(tmp_path, _universe(), name="flat"), as_of=LATER)
        target = next(v for v in values if v.identifier == "isin:US0000000099")
        assert target.verdict == "in noise"


class TestTheNoiseGate:
    def test_a_call_inside_the_peer_spread_is_not_a_call(self, tmp_path: Path) -> None:
        """The property that stops scatter being read as signal. A bond
        80bp from a median whose peers span 400bp has said nothing."""
        values = peer_values(_store(tmp_path, _universe(cheap_price=99.0), name="n"), as_of=LATER)
        target = next(v for v in values if v.identifier == "isin:US0000000099")
        assert target.in_noise == (abs(target.residual_bp) <= target.peer_dispersion_pct * 100.0)

    def test_significant_calls_sort_above_the_noise(self, tmp_path: Path) -> None:
        """Ordering by residual alone puts the widest peer groups on top,
        which is the defect the issuer-curve ranking already had to fix."""
        values = peer_values(_store(tmp_path, _universe(cheap_price=85.0), name="s"), as_of=LATER)
        verdicts = [v.in_noise for v in values]
        assert verdicts == sorted(verdicts)


class TestItSaysHowWeakTheMatchIs:
    def test_the_missing_dimensions_travel_with_every_call(self, tmp_path: Path) -> None:
        """Rating, sector and seniority are why a peer call is weaker than
        a curve call. Carried on the value rather than described once in a
        docstring nobody reading the screen will see."""
        values = peer_values(_store(tmp_path, _universe()), as_of=LATER)
        assert all(v.missing_dimensions == ("sector", "rating", "seniority") for v in values)
        assert all("currency" in v.dimensions for v in values)

    def test_the_peer_count_is_published_against_the_universe(self, tmp_path: Path) -> None:
        """The ratio is the honest measure of selectivity: on the live
        store a peer group is routinely 226 of 233 bonds, which is a market
        level wearing the word peer."""
        values = peer_values(_store(tmp_path, _universe()), as_of=LATER)
        assert all(v.peer_count <= v.universe_size for v in values)
        assert all(v.peer_count >= MIN_PEERS for v in values)

    def test_the_issuer_is_named(self, tmp_path: Path) -> None:
        """An LEI is unreadable, and a call nobody can attribute to a
        company is one they can neither act on nor check. Names covered
        only fitted issuers until this tab needed them."""
        values = peer_values(_store(tmp_path, _universe()), as_of=LATER)
        assert all(v.issuer for v in values)


class TestItRefusesRatherThanGuesses:
    def test_too_few_bonds_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(NoPeersError):
            peer_values(
                _store(tmp_path, [("US0000000001", "AA", 100.0, 5)], name="tiny"), as_of=LATER
            )
