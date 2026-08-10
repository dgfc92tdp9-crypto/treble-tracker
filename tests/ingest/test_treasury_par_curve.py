"""The US Treasury daily par yield curve (CMT).

Chosen for durability: public domain under 17 USC 105, no API key, no free
tier to withdraw, `robots.txt` clean, and published every business day
since 1990. There is no licence here that can be revoked and no credential
that can expire, which is the standard the rest of the source list gets
judged against.

The tests are mostly about the parser refusing rather than guessing,
because the failure that costs you a curve is not an exception — it is a
tenor quietly going missing while the fit still succeeds.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from treble.ingest.base import RawPayload
from treble.ingest.treasury_curve import (
    FIELD,
    TENORS,
    TreasuryCurveAdapter,
    UnknownColumnError,
    latest_curve,
    tenor_subject,
)
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadStore

FETCHED = datetime(2026, 8, 8, 22, 0, tzinfo=UTC)

#: A recorded payload — the first twenty trading days of the real published
#: file, bytes unaltered. Hand-written CSV would test this parser against
#: this author's idea of Treasury's format, which is exactly the format
#: that never breaks. The recorded one carries the quoting, the header
#: spellings and the "1.5 Month" column as actually served.
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "treasury"
REAL = (FIXTURE / "daily_par_yield_curve_2026.csv").read_bytes()


@pytest.fixture
def adapter(tmp_path: Path) -> TreasuryCurveAdapter:
    return TreasuryCurveAdapter(PayloadStore(tmp_path / "p"), IngestLog(tmp_path / "l.db"))


def _parse(adapter: TreasuryCurveAdapter, body: bytes) -> object:
    return adapter.parse(
        RawPayload(data=body, source_uri="https://example.invalid/x", fetched_at=FETCHED),
        "0" * 64,
    )


class TestItReadsThePublishedFile:
    def test_every_tenor_on_every_day_becomes_a_fact(self, adapter: TreasuryCurveAdapter) -> None:
        """Counted against the fixture rather than a literal, so trimming
        or extending the recorded file cannot silently weaken this."""
        days = len(REAL.decode().strip().splitlines()) - 1
        assert len(_parse(adapter, REAL).facts) == 14 * days  # type: ignore[attr-defined]

    def test_the_american_date_is_read_as_month_first(self, adapter: TreasuryCurveAdapter) -> None:
        """08/07/2026 is 7 August, not 8 July. The first version of this
        parser reversed the tuple and produced the latter, and a smoke test
        that re-wrote the expression by hand rather than calling the parser
        agreed with it."""
        from datetime import date

        days = {f.effective_from for f in _parse(adapter, REAL).facts}  # type: ignore[attr-defined]
        assert max(days) == date(2026, 8, 7)
        # The reversed bug turned 08/07 into 8 July. Asserting no row lands
        # in July catches it across the whole fixture rather than at one
        # date that might happen to be ambiguous.
        assert all(d.month == 8 or d.day > 12 for d in days)

    def test_percent_becomes_a_decimal_rate(self, adapter: TreasuryCurveAdapter) -> None:
        """Treasury publishes 4.65; every rate in this system is a decimal,
        and a curve mixing the two is off by a factor of a hundred at
        whichever points came from the wrong source."""
        ten_year = [
            f
            for f in _parse(adapter, REAL).facts  # type: ignore[attr-defined]
            if str(f.subject) == str(tenor_subject("10Y")) and f.effective_from.day == 7
        ]
        assert ten_year[0].value == pytest.approx(0.0465)

    def test_the_field_and_subject_are_stable(self, adapter: TreasuryCurveAdapter) -> None:
        """Replay depends on these being derived, not generated (I5)."""
        assert str(tenor_subject("10Y")) == "govt:UST-CMT:10Y"
        assert {f.field for f in _parse(adapter, REAL).facts} == {FIELD}  # type: ignore[attr-defined]

    def test_the_shape_is_a_plausible_curve(self, adapter: TreasuryCurveAdapter) -> None:
        """An arithmetic test would pass on a curve that was upside down.
        Short rates below long ones, and everything inside a range no US
        curve has left in decades."""
        curve = latest_curve(list(_parse(adapter, REAL).facts))  # type: ignore[attr-defined]
        assert curve["1M"] < curve["10Y"]
        assert all(0.0 < rate < 0.25 for rate in curve.values())


class TestItRefusesRatherThanGuesses:
    def test_an_unmapped_column_is_an_error_not_a_shrug(
        self, adapter: TreasuryCurveAdapter
    ) -> None:
        """Treasury added "1.5 Month" in 2024 without an announcement this
        repository would have seen. A parser that ignored unknown columns
        would have dropped it silently — the curve loses a point, the fit
        still succeeds, and nothing anywhere says so."""
        body = REAL.replace(b'"1 Mo"', b'"1 Week"')
        with pytest.raises(UnknownColumnError, match="1 Week"):
            _parse(adapter, body)

    def test_an_empty_document_is_a_failure(self, adapter: TreasuryCurveAdapter) -> None:
        """A 200 carrying no rows is what a changed URL or a blocked client
        looks like. Logged as a successful fetch it would be indis-
        tinguishable from a quiet day."""
        with pytest.raises(ValueError, match="no rows"):
            _parse(adapter, b'Date,"1 Mo"\n')

    def test_a_blank_cell_is_skipped_rather_than_read_as_zero(
        self, adapter: TreasuryCurveAdapter
    ) -> None:
        """Treasury leaves a tenor blank when it was not published — the
        30Y was suspended entirely from 2002 to 2006. A zero would be a
        0% thirty-year yield, which any curve fit would happily accept."""
        lines = REAL.decode().strip().splitlines()
        # Blank the 30Y on the most recent day only.
        lines[1] = lines[1].rsplit(",", 1)[0] + ","
        body = ("\n".join(lines) + "\n").encode()
        subjects = [str(f.subject) for f in _parse(adapter, body).facts]  # type: ignore[attr-defined]
        full = [str(f.subject) for f in _parse(adapter, REAL).facts]  # type: ignore[attr-defined]
        thirty = str(tenor_subject("30Y"))
        assert subjects.count(thirty) == full.count(thirty) - 1


class TestTheSourceIsDeclaredHonestly:
    def test_it_is_not_redistribution_restricted(self) -> None:
        """Public domain. If this were ever flipped on, the export guard
        would start refusing a US government work."""
        assert TreasuryCurveAdapter.meta.redistribution_restricted is False

    def test_it_declares_a_daily_cadence(self) -> None:
        """So the health report can notice when Treasury's publication —
        or our access to it — stops."""
        assert TreasuryCurveAdapter.meta.expected_cadence_days == 1.0

    def test_the_registry_knows_it(self) -> None:
        from treble.ingest.registry import all_sources

        assert "treasury-curve" in all_sources()

    def test_all_fourteen_tenors_are_mapped(self) -> None:
        assert len(TENORS) == 14
        assert TENORS["1.5 Month"] == "6W"
