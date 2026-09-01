"""A parser may not change what it produces without changing its version.

I5 states it: *"Parsers are pure functions of (raw payload, parser
version)."* Nothing enforced it, and the live store shows what that costs.

`swap:USD-SOFR-OIS:10Y` TRADE_COUNT for 2026-07-13 is stored **twice**, as
227 and as 234 — one payload, one ingest-log entry, one provenance record,
`extractor_version` "1" on both. The parser produced 227 at one point and
234 at another while still calling itself version 1, so provenance cannot
tell the two apart and the visibility window settles them by `TIE_BREAK`,
which for equal knowledge times is alphabetical ordering. Two contradictory
facts, and the one that surfaces is chosen by arithmetic on the value.

286 facts across dtcc-sdr are in that state.

**The digest is the enforcement.** Each adapter's parse output over its
recorded fixture is hashed and the hash is committed. Change what a parser
produces and this fails, naming the adapter and telling you to bump
`parser_version` — at which point provenance distinguishes the two readings
and the newer one is a legitimate restatement rather than a contradiction.

Regenerate with ``TREBLE_REGEN_PARSER_DIGESTS=1``, and review the diff like
code: a changed digest with an unchanged version is exactly the defect.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from treble.ingest.base import ParsedBatch

DIGESTS = Path(__file__).parent / "parser_digests.json"


def digest(batch: ParsedBatch) -> str:
    """A stable hash of what a parse produced.

    Facts are sorted, so a parser that changes only the *order* it emits
    them is not reported — order carries no meaning and flagging it would
    make the check noisy for a change that cannot affect an answer.

    Provenance is excluded: it carries the retrieval timestamp, which
    differs on every run and would make every digest unstable.
    """
    rows = sorted(
        (
            str(fact.subject),
            fact.field,
            str(fact.value),
            fact.effective_from.isoformat(),
            fact.effective_to.isoformat() if fact.effective_to else "",
        )
        for fact in batch.facts
    )
    payload = json.dumps(rows, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _recorded() -> dict[str, dict[str, str]]:
    if not DIGESTS.exists():
        return {}
    data = json.loads(DIGESTS.read_text())
    assert isinstance(data, dict)
    return data


def record(source_id: str, parser_version: str, value: str) -> None:
    """Write a digest, keyed by adapter and parser version."""
    data = _recorded()
    data.setdefault(source_id, {})[str(parser_version)] = value
    DIGESTS.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def check(source_id: str, parser_version: str, batch: ParsedBatch) -> None:
    """Assert this parse matches what was recorded for this version.

    Call from an adapter's own fixture test, where the fixture and the
    expected parse already live. Centralising the *discovery* of fixtures
    was rejected: every adapter builds its payload differently, and a
    discovery layer clever enough to cope would be a second, untested
    implementation of every adapter's test.
    """
    actual = digest(batch)
    if os.environ.get("TREBLE_REGEN_PARSER_DIGESTS"):
        record(source_id, parser_version, actual)
        return
    expected = _recorded().get(source_id, {}).get(str(parser_version))
    if expected is None:
        pytest.fail(
            f"{source_id} has no recorded digest for parser_version "
            f"{parser_version!r}. Run with TREBLE_REGEN_PARSER_DIGESTS=1 and "
            "commit the result."
        )
    assert actual == expected, (
        f"{source_id} parser_version {parser_version} now produces different facts "
        f"from the same fixture ({expected} -> {actual}).\n"
        "  If the change is intended, bump `parser_version` on the adapter and "
        "regenerate — a new version makes the new reading a restatement that "
        "provenance can distinguish.\n"
        "  If it is not intended, this is the bug: the live store already holds "
        "286 facts written by two different behaviours of one version, "
        "indistinguishable and settled by alphabet."
    )


class TestTheDigestItself:
    """The check is worth nothing if it cannot tell two parses apart."""

    def _batch(self, value: float) -> ParsedBatch:
        from datetime import UTC, date, datetime

        from treble.core.facts import Fact
        from treble.core.identifiers import TUID
        from treble.core.provenance import ExtractionMethod, Provenance

        record_ = Provenance(
            source_system="test",
            source_uri="https://example.invalid/x",
            retrieved_at=datetime(2026, 9, 1, tzinfo=UTC),
            method=ExtractionMethod.API,
            extractor_version="1",
        )
        return ParsedBatch(
            facts=[
                Fact(
                    subject=TUID("swap:USD-SOFR-OIS:10Y"),
                    field="TRADE_COUNT",
                    value=value,
                    effective_from=date(2026, 7, 13),
                    knowledge_from=datetime(2026, 8, 2, tzinfo=UTC),
                    provenance_id=record_.id,
                )
            ],
            provenance=[record_],
        )

    def test_the_same_facts_hash_the_same(self) -> None:
        assert digest(self._batch(234.0)) == digest(self._batch(234.0))

    def test_the_live_defect_would_be_caught(self) -> None:
        """227 against 234 — the two values the store actually holds for one
        payload under one version."""
        assert digest(self._batch(227.0)) != digest(self._batch(234.0))

    def test_provenance_timing_does_not_move_the_digest(self) -> None:
        """Retrieval time differs on every run. A digest that included it
        would fail constantly and be regenerated without being read."""
        from datetime import UTC, datetime

        from treble.core.provenance import ExtractionMethod, Provenance

        batch = self._batch(234.0)
        later = Provenance(
            source_system="test",
            source_uri="https://example.invalid/x",
            retrieved_at=datetime(2027, 1, 1, tzinfo=UTC),
            method=ExtractionMethod.API,
            extractor_version="1",
        )
        moved = ParsedBatch(facts=list(batch.facts), provenance=[later])
        assert digest(moved) == digest(batch)
