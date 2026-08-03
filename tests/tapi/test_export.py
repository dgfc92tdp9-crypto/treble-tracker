"""The bulk-export guard (spec §9.3, §8.5).

`redistribution_restricted` was declared on thirteen adapters and read by
nothing for the whole of Phase 1 — the flag's own docstring named a
bulk-export guard that did not exist, and the only read anywhere was a test
asserting the flag's *value*. Asserting a declaration is not testing a
behaviour, and that gap is the fourth of its kind in this project.

So these tests are written against the behaviour: a restricted source must
be observably absent from an export, not merely marked. `test_the_guard_is
_wired_to_the_real_registry` is the one that would have failed for the whole
of Phase 1.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from treble.core.facts import Fact
from treble.core.provenance import ExtractionMethod, Provenance, ProvenanceId
from treble.ingest.registry import all_sources, restricted_source_ids
from treble.tapi.export import (
    ExportRefusedError,
    check_selection,
    filter_exportable,
)

KNOWN = datetime(2026, 8, 3, 6, 0, tzinfo=UTC)
DAY = date(2026, 7, 31)


def provenance(source: str) -> Provenance:
    return Provenance(
        source_system=source,
        source_uri=f"https://example.invalid/{source}",
        retrieved_at=KNOWN,
        method=ExtractionMethod.API,
        extractor_version="1",
        payload_hash="0" * 64,
    )


def fact(subject: str, provenance_id: ProvenanceId) -> Fact:
    return Fact(
        subject=subject,
        field="PX_LAST",
        value=100.0,
        effective_from=DAY,
        effective_to=DAY,
        knowledge_from=KNOWN,
        provenance_id=provenance_id,
    )


def lookup(mapping: dict[ProvenanceId, str]):  # type: ignore[no-untyped-def]
    return lambda pid: mapping.get(pid)


class TestRestrictedSourcesAreWithheld:
    def test_a_restricted_source_does_not_leave(self) -> None:
        open_source, restricted = provenance("fred"), provenance("trace-api")
        result = filter_exportable(
            [fact("fred:DGS10", open_source.id), fact("cusip:912810UT3", restricted.id)],
            source_of=lookup({open_source.id: "fred", restricted.id: "trace-api"}),
            restricted=frozenset({"trace-api"}),
        )
        assert [f.subject for f in result.facts] == ["fred:DGS10"]
        assert result.withheld_by_source == {"trace-api": 1}

    def test_withholding_is_reported_not_silent(self) -> None:
        """A warehouse that received 90% of a universe and believed it had
        all of it would compute coverage, index weights and risk aggregates
        against a hole it could not see."""
        restricted = provenance("dtcc-sdr")
        result = filter_exportable(
            [fact(f"swap:USD-SOFR-OIS:{n}Y", restricted.id) for n in (2, 5, 10)],
            source_of=lookup({restricted.id: "dtcc-sdr"}),
            restricted=frozenset({"dtcc-sdr"}),
        )
        assert result.facts == ()
        assert result.withheld_by_source == {"dtcc-sdr": 3}
        assert result.withheld_total == 3
        assert result.is_complete is False

    def test_a_clean_export_says_so(self) -> None:
        open_source = provenance("fred")
        result = filter_exportable(
            [fact("fred:DGS10", open_source.id)],
            source_of=lookup({open_source.id: "fred"}),
            restricted=frozenset({"trace-api"}),
        )
        assert result.is_complete is True
        assert result.withheld_by_source == {}

    def test_an_unattributable_fact_is_withheld(self) -> None:
        """I1 makes provenance part of a value. A fact whose source cannot
        be resolved cannot be shown to come from an unrestricted one, so it
        does not leave — the safe direction when the answer is unknown."""
        orphan = provenance("fred")
        result = filter_exportable(
            [fact("fred:DGS10", orphan.id)],
            source_of=lookup({}),
            restricted=frozenset(),
        )
        assert result.facts == ()
        assert result.withheld_unattributed == 1
        assert result.is_complete is False


class TestTheGuardIsActuallyWired:
    """The tests that would have failed for the whole of Phase 1."""

    def test_the_registry_finds_the_shipped_adapters(self) -> None:
        """Discovered, not listed. A hand-maintained list is the same defect
        in a new place: an adapter added without an entry would export
        freely and nothing would say so."""
        sources = all_sources()
        assert len(sources) >= 13
        assert {"fred", "trace-api", "dtcc-sdr", "sec-nport"} <= set(sources)

    def test_the_restricted_set_is_not_empty(self) -> None:
        """An empty restricted set reads exactly like a clean bill of
        health, and is what a registry nothing had populated would return."""
        assert restricted_source_ids(), "no restricted sources — is discovery running?"

    def test_trace_and_dtcc_are_restricted(self) -> None:
        assert {"trace-api", "dtcc-sdr"} <= restricted_source_ids()

    def test_the_guard_is_wired_to_the_real_registry(self) -> None:
        """The kill-test. Called with no explicit `restricted` set — the way
        production calls it — a DTCC fact must still be withheld. If the
        guard ever stops consulting the registry, only this fails.
        """
        dtcc = provenance("dtcc-sdr")
        result = filter_exportable(
            [fact("swap:USD-SOFR-OIS:10Y", dtcc.id)],
            source_of=lookup({dtcc.id: "dtcc-sdr"}),
        )
        assert result.facts == ()
        assert result.withheld_by_source == {"dtcc-sdr": 1}

    def test_an_open_source_still_exports_under_the_real_registry(self) -> None:
        """The guard must not be a blanket refusal. A rule that blocked
        everything would pass the test above and make bulk export useless,
        which is a headline capability (§8.5)."""
        fred = provenance("fred")
        result = filter_exportable(
            [fact("fred:DGS10", fred.id)], source_of=lookup({fred.id: "fred"})
        )
        assert len(result.facts) == 1
        assert result.is_complete is True


class TestLicensedIdentifierNamespaces:
    """§9.3: resolution and display work; bulk export of the master does not."""

    @pytest.mark.parametrize("namespace", ["cusip", "isin", "sedol", "CUSIP", "cusip:912810UT3"])
    def test_a_licensed_namespace_is_refused(self, namespace: str) -> None:
        with pytest.raises(ExportRefusedError, match="licensed identifier"):
            check_selection(namespace)

    @pytest.mark.parametrize("namespace", ["figi", "lei", "cik", "fred", "swap"])
    def test_an_open_namespace_is_allowed(self, namespace: str) -> None:
        """FIGI is free and openly redistributable — it is what makes an open
        workstation legally possible, and blocking it would be the guard
        misfiring on the one identifier that needs no protection."""
        check_selection(namespace)

    def test_the_refusal_explains_what_still_works(self) -> None:
        """A refusal that reads as 'CUSIP is unavailable' would send a user
        looking for a workaround to something that already works."""
        with pytest.raises(ExportRefusedError) as raised:
            check_selection("cusip")
        message = str(raised.value)
        assert "single" in message and "unaffected" in message
