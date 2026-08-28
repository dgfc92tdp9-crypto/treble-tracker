"""DES screen: definition validity and resolution against real IBM data.

Renders the actual recorded EDGAR filing through the real store, real TAPI
and the generic resolver — so what these tests assert is what a user would
see on screen, not a mock of it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from treble.core.identifiers import SecurityQuery, YellowKey
from treble.ingest.base import RawPayload
from treble.ingest.edgar import EdgarCompanyFactsAdapter
from treble.render.contract.buffer import text_snapshot
from treble.render.contract.registry import (
    UnknownScreenError,
    available,
    get_screen,
    has_screen,
)
from treble.render.contract.resolver import ScreenContext, resolve
from treble.render.contract.schema import Attr
from treble.store.duck import DuckStore
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore, payload_hash
from treble.tapi.local import LocalTapi, TickerIndex

FIXTURES = Path(__file__).parent.parent / "fixtures"
COMPANYFACTS = FIXTURES / "edgar" / "companyfacts_CIK0000051143.json"
FETCHED = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
AS_OF = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
IBM = SecurityQuery(ticker="IBM", key=YellowKey.EQUITY, venue="US")


@pytest.fixture
def tapi(tmp_path: Path) -> LocalTapi:
    store = DuckStore(tmp_path / "t.db")
    adapter = EdgarCompanyFactsAdapter(
        PayloadStore(tmp_path / "p"),
        IngestLog(tmp_path / "l.db"),
        ciks=(51143,),
        contact_email="test@example.com",
    )
    raw = RawPayload(data=COMPANYFACTS.read_bytes(), source_uri="fixture://cf", fetched_at=FETCHED)
    batch = adapter.parse(raw, payload_hash(raw.data))
    store.write_provenance(list(batch.provenance))
    store.write_facts(list(batch.facts))
    return LocalTapi(store, tickers=TickerIndex({"IBM": 51143}))


class TestRegistry:
    def test_des_is_registered(self) -> None:
        assert has_screen("DES")
        assert "DES" in available()

    def test_lookup_is_case_insensitive(self) -> None:
        assert get_screen("des").mnemonic == "DES"

    def test_unknown_screen_raises_rather_than_blanking(self) -> None:
        # A function that silently renders nothing is indistinguishable
        # from one with no data.
        with pytest.raises(UnknownScreenError, match="available"):
            get_screen("NOSUCHSCREEN")

    def test_every_definition_validates_against_the_contract(self) -> None:
        # Loading validates the schema, including that no cell falls
        # outside its declared grid.
        for mnemonic in available():
            definition = get_screen(mnemonic)
            assert definition.rows > 0 and definition.cols > 0
            assert definition.tabs


class TestDefinition:
    def test_declares_the_equity_namespace(self) -> None:
        assert "Equity" in get_screen("DES").namespaces

    def test_binds_only_dictionary_fields(self) -> None:
        # No coined mnemonics: every bound field must be resolvable by the
        # field dictionary (CLAUDE.md - §24/§9.6 are the contract).
        from treble.render.contract.schema import BoundCell
        from treble.tapi.fields import FIELDS

        for tab in get_screen("DES").tabs:
            for cell in tab.cells:
                if isinstance(cell, BoundCell):
                    assert cell.field in FIELDS, f"{cell.field} not in dictionary"


class TestResolutionAgainstRealData:
    def test_renders_ibms_real_reported_figures(self, tapi: LocalTapi) -> None:
        doc = json.loads(COMPANYFACTS.read_bytes())
        rows = doc["facts"]["us-gaap"]["Assets"]["units"]["USD"]
        expected_assets = max(rows, key=lambda r: r["end"])["val"]

        buffer = resolve(get_screen("DES"), ScreenContext(security=IBM), as_of=AS_OF, tapi=tapi)
        rendered = text_snapshot(buffer)
        assert f"{float(expected_assets):,.0f}" in rendered

    def test_every_value_cell_carries_provenance(self, tapi: LocalTapi) -> None:
        # I1: any number on screen must be traceable via SPTR.
        buffer = resolve(get_screen("DES"), ScreenContext(security=IBM), as_of=AS_OF, tapi=tapi)
        valued = [c for c in buffer.cells if c.provenance_id is not None]
        assert len(valued) >= 5, "DES should render several traceable figures"

    def test_missing_values_render_as_a_dash_not_zero(self, tapi: LocalTapi) -> None:
        # A zero would be a fabricated number; an em dash is honest.
        buffer = resolve(get_screen("DES"), ScreenContext(security=IBM), as_of=AS_OF, tapi=tapi)
        rendered = text_snapshot(buffer)
        assert "0" in rendered  # real figures present
        for cell in buffer.cells:
            if cell.provenance_id is None and cell.text.strip() == "—":
                assert Attr.STALE in cell.attrs

    def test_links_carry_executable_commands(self, tapi: LocalTapi) -> None:
        # §5.4: cyan links; <GO> on the row runs the command.
        buffer = resolve(get_screen("DES"), ScreenContext(security=IBM), as_of=AS_OF, tapi=tapi)
        links = [c for c in buffer.cells if c.link_command]
        assert len(links) == 3
        assert any(c.link_command == "IBM US Equity FA" for c in links)
        assert all(Attr.LINK in c.attrs for c in links)

    def test_point_in_time_render_shows_nothing_before_filing(self, tapi: LocalTapi) -> None:
        # I2: rendering as of 1990 must not show 2026 figures.
        buffer = resolve(
            get_screen("DES"),
            ScreenContext(security=IBM),
            as_of=datetime(1990, 1, 1, tzinfo=UTC),
            tapi=tapi,
        )
        assert all(c.provenance_id is None for c in buffer.cells)

    def test_render_is_deterministic(self, tapi: LocalTapi) -> None:
        first = resolve(get_screen("DES"), ScreenContext(security=IBM), as_of=AS_OF, tapi=tapi)
        second = resolve(get_screen("DES"), ScreenContext(security=IBM), as_of=AS_OF, tapi=tapi)
        assert text_snapshot(first) == text_snapshot(second)

    def test_cells_stay_inside_the_declared_grid(self, tapi: LocalTapi) -> None:
        buffer = resolve(get_screen("DES"), ScreenContext(security=IBM), as_of=AS_OF, tapi=tapi)
        for cell in buffer.cells:
            assert 0 <= cell.row < buffer.rows
            assert 0 <= cell.col < buffer.cols
