"""Say "we cannot attribute a value to this" without deleting anything.

Some facts are not wrong. They are **unattributable**: correct numbers filed
under a key that does not identify what they describe, so nothing can say
which of them is about what.

The live case. `sec-nport` once keyed OTC derivatives as
`otc:<counterparty>:<kind>:<date>`, which put every contract a fund held with
one broker on a single subject. A fund running fifteen index futures against
one clearing broker produced fifteen facts on one key, all true of *some*
contract and none attributable to a particular one. `derivative_subject` now
builds a six-segment key that separates them, but the old rows remain, and
37 keys still hold contradictory values at their newest knowledge time.

They cannot be corrected the way a wrong value is corrected. Re-ingesting
writes to the *new* subjects; the old ones are simply never generated again,
so nothing supersedes them. And they cannot be deleted: I2 is inserts only,
and those rows are a true record of what was believed.

## What this writes instead

A **null fact at a new knowledge time** — `value=None`, which the store
already distinguishes from every stated value by `value_kind` and which the
screens render as an em dash. Latest-knowledge-wins then resolves the key to
"no value", which is the honest answer: we hold numbers here and cannot say
what any of them is about.

## Why this is not a special case bolted onto I1

The provenance is `DERIVED` with `input_ids` naming every provenance record
whose rows are being retracted. That is precisely what I1 asks of a derived
value — "derived values carry a provenance record referencing their inputs,
forming a DAG" — so `SPTR` on a retracted key walks back to the exact rows
that could not be told apart, and the reason is on the record rather than in
a commit message.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance

#: The `source_system` a retraction is filed under.
#:
#: Not the source's own name. The source did not say this; we did, about
#: what the source gave us. Filing it as `sec-nport` would make the health
#: report count it as that source flowing and would put a statement we
#: authored under a name that implies SEC provenance.
RETRACTION_SOURCE = "treble-correction"


@dataclass(frozen=True)
class Unattributable:
    """One key whose stored values cannot be attributed to anything."""

    subject: TUID
    field: str
    effective_from: date
    effective_to: date | None
    #: Provenance of the rows being retracted — the DAG inputs (I1).
    inputs: tuple[str, ...]


def retraction(
    item: Unattributable, *, reason: str, at: datetime | None = None
) -> tuple[Provenance, Fact]:
    """The provenance and the null fact that retract one key. Pure.

    ``reason`` becomes the `source_uri`, because that field is what `SPTR`
    shows a person asking where a value came from, and for a retraction the
    honest answer is a sentence rather than a URL.
    """
    if not item.inputs:
        raise ValueError(
            f"{item.subject}/{item.field}: a retraction with no inputs would assert "
            "that nothing is known here without saying what it supersedes"
        )
    known_at = at or datetime.now(UTC)
    record = Provenance(
        source_system=RETRACTION_SOURCE,
        source_uri=reason,
        retrieved_at=known_at,
        method=ExtractionMethod.DERIVED,
        extractor_version="1",
        confidence=1.0,
        input_ids=tuple(sorted(item.inputs)),
    )
    fact = Fact(
        subject=item.subject,
        field=item.field,
        value=None,
        effective_from=item.effective_from,
        effective_to=item.effective_to,
        knowledge_from=known_at,
        provenance_id=record.id,
    )
    return record, fact


def retract_all(
    items: Sequence[Unattributable], *, reason: str, at: datetime | None = None
) -> tuple[list[Provenance], list[Fact]]:
    """Retractions for every item, sharing one knowledge instant.

    One instant on purpose: these are a single act of correction, and
    spreading them across microseconds would make the order they were
    written look like information.
    """
    known_at = at or datetime.now(UTC)
    records: list[Provenance] = []
    facts: list[Fact] = []
    for item in items:
        record, fact = retraction(item, reason=reason, at=known_at)
        records.append(record)
        facts.append(fact)
    return records, facts


__all__ = ["RETRACTION_SOURCE", "Unattributable", "retract_all", "retraction"]
