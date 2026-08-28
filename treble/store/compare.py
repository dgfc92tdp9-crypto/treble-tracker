"""Compare two stores, fact for fact.

Written for the replay check, and deliberately not part of it: the question
"do these two stores hold the same facts" is worth answering on its own, and
a comparison living inside the thing it validates is a comparison that gets
adjusted until it passes.

## Two comparisons, because they answer different questions

``exact`` covers all twelve fact columns including ``provenance_id``. It is
the strongest claim: byte-identical rows.

``content`` covers the eleven columns excluding ``provenance_id``. Provenance
ids are derived from their fields, so renaming a `source_system` changes the
id on every fact that source ever produced while changing nothing about the
facts. Measured on the live store, three sources had been renamed and 8.07
million facts differed on that column and no other. A comparison with only
the exact form would have called that a divergence and been useless.

## Order-independent hashing

`sum(hash(...))` rather than `bit_xor`, for the reason `cold.py` gives: XOR
cancels in pairs, so a store holding every fact exactly twice would hash
identically to one holding each once — which is precisely the corruption
worth catching. Cast to HUGEINT so summing millions of 64-bit hashes cannot
wrap into a collision.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from treble.store.schema import FACT_COLUMNS, FACT_PROJECTION

#: The eleven columns that describe the fact rather than its paperwork.
CONTENT_COLUMNS = tuple(c for c in FACT_COLUMNS if c != "provenance_id")
_CONTENT = ", ".join(CONTENT_COLUMNS)


@dataclass(frozen=True)
class Digest:
    """A row count and an order-independent hash."""

    rows: int
    checksum: int

    def __bool__(self) -> bool:
        return self.rows > 0


@dataclass(frozen=True)
class SourceComparison:
    """How one source's facts differ between two stores."""

    source: str
    left: int
    right: int
    #: Rows on the right whose *content* is absent from the left, and the
    #: reverse. Kept as two numbers rather than one signed difference
    #: because a subset and a superset are different findings and a store
    #: can be both at once — `gleif-rr` was.
    only_right: int
    only_left: int
    exact: bool
    same_content: bool

    @property
    def identical(self) -> bool:
        return self.exact

    @property
    def verdict(self) -> str:
        if self.exact:
            return "identical"
        if self.same_content:
            return "identical content, provenance differs"
        if self.right == 0:
            return "not reproduced"
        if self.only_right == 0 and self.only_left == 0:
            return "same content set"
        if self.only_right == 0:
            return "subset"
        if self.only_left == 0:
            return "superset"
        return "diverged both ways"


class StoreComparison:
    """Two stores attached to one connection, queryable side by side.

    Attaches read-only. A comparison that could write to either store is a
    comparison that can change its own answer.
    """

    def __init__(self, left: Path, right: Path) -> None:
        self._conn = duckdb.connect()
        self._conn.execute(f"ATTACH '{_escape(left)}' AS l (READ_ONLY)")
        self._conn.execute(f"ATTACH '{_escape(right)}' AS r (READ_ONLY)")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> StoreComparison:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def sources(self) -> tuple[str, ...]:
        rows = self._conn.execute(
            "SELECT DISTINCT source_system FROM l.provenance "
            "UNION SELECT DISTINCT source_system FROM r.provenance ORDER BY 1"
        ).fetchall()
        return tuple(str(r[0]) for r in rows)

    def digest(self, side: str, sources: tuple[str, ...], *, columns: str) -> Digest:
        if not sources:
            return Digest(rows=0, checksum=0)
        row = self._conn.execute(
            f"SELECT count(*), coalesce(sum(hash({columns})::HUGEINT), 0) "  # noqa: S608
            f"FROM {side}.facts f JOIN {side}.provenance p ON p.id = f.provenance_id "
            f"WHERE {_inlist(sources)}"
        ).fetchone()
        return Digest(rows=int(row[0]), checksum=int(row[1])) if row else Digest(0, 0)

    def only_in(self, side: str, sources: tuple[str, ...], other: tuple[str, ...]) -> int:
        """Rows on ``side`` whose content is absent from the other side."""
        if not sources:
            return 0
        other_side = "r" if side == "l" else "l"
        mine = _select(side, sources)
        empty = f"SELECT {_CONTENT} FROM l.facts WHERE 0"  # noqa: S608
        theirs = _select(other_side, other) if other else empty
        row = self._conn.execute(
            f"SELECT count(*) FROM (({mine}) EXCEPT ({theirs}))"  # noqa: S608
        ).fetchone()
        return int(row[0]) if row else 0

    def compare(
        self,
        name: str,
        left_sources: tuple[str, ...],
        right_sources: tuple[str, ...],
    ) -> SourceComparison:
        exact_l = self.digest("l", left_sources, columns=FACT_PROJECTION)
        exact_r = self.digest("r", right_sources, columns=FACT_PROJECTION)
        content_l = self.digest("l", left_sources, columns=_CONTENT)
        content_r = self.digest("r", right_sources, columns=_CONTENT)
        return SourceComparison(
            source=name,
            left=exact_l.rows,
            right=exact_r.rows,
            only_right=self.only_in("r", right_sources, left_sources),
            only_left=self.only_in("l", left_sources, right_sources),
            exact=exact_l == exact_r,
            same_content=content_l == content_r,
        )

    def compare_all(
        self, aliases: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] | None = None
    ) -> tuple[SourceComparison, ...]:
        """Every source, with renamed ones grouped.

        ``aliases`` maps a display name to (left source ids, right source
        ids). Anything not named pairs with itself — so the common case
        needs no configuration and a rename is stated explicitly rather
        than inferred from a count that happened to match.
        """
        groups = dict(aliases or {})
        claimed = {s for pair in groups.values() for side in pair for s in side}
        for source in self.sources():
            if source not in claimed:
                groups[source] = ((source,), (source,))
        return tuple(self.compare(name, *groups[name]) for name in sorted(groups))


def _escape(path: Path) -> str:
    return str(path).replace("'", "''")


def _inlist(sources: tuple[str, ...]) -> str:
    return "p.source_system IN (" + ", ".join(f"'{s}'" for s in sources) + ")"


def _select(side: str, sources: tuple[str, ...]) -> str:
    return (
        f"SELECT {_CONTENT} FROM {side}.facts f "  # noqa: S608
        f"JOIN {side}.provenance p ON p.id = f.provenance_id WHERE {_inlist(sources)}"
    )


__all__ = [
    "CONTENT_COLUMNS",
    "Digest",
    "SourceComparison",
    "StoreComparison",
]
