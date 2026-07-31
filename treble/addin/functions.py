"""Spreadsheet functions — `TDP`, `TDH`, `TDS`, `TQL` (spec §4.1, §4.2).

    =TDP("IBM US Equity", "PX_LAST")
    =TDH("IBM US Equity", "PX_LAST", "1/1/2024", "12/31/2024", "Per=D")
    =TDS("SPX Index", "INDX_MWEIGHT_HIST", "END_DATE_OVERRIDE=20240630")
    =TQL("get(px_last) for(bonds()) with(dates=range(-1Y,0D))")

The logic lives here as ordinary functions over a TAPI view, and the
xlwings layer in `udf.py` is a thin shell around them. That split is
deliberate: a spreadsheet function that can only be tested inside Excel is
a spreadsheet function that does not get tested.

**Everything goes through TAPI (I7).** The add-in is a client like any
other surface, so a figure in a cell is the same figure the screen shows,
resolved by the same path.

**Errors are returned, not raised.** A raising formula shows `#VALUE!` and
says nothing, so a user cannot tell a wrong ticker from a broken install.
These return the reason as the cell's text — the same choice the status
line makes on a screen, for the same reason.

**No redistribution meter.** Spec §4.1 is explicit that the spreadsheet
surface carries no rate limit and no redistribution restriction: the data
is public or contributed, and there is nothing to meter.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Protocol

from treble.core.identifiers import SecurityQuery, parse_security
from treble.tapi.types import FieldResult

#: What a cell shows when a value is genuinely absent. The same em dash the
#: screens use, so "not reported" looks identical in both surfaces.
BLANK = "—"


class TapiView(Protocol):
    def field(
        self,
        security: SecurityQuery | None,
        mnemonic: str,
        overrides: dict[str, str],
        *,
        as_of: datetime,
    ) -> FieldResult: ...

    def series(
        self, security: SecurityQuery | None, binding: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]: ...


def parse_overrides(arguments: tuple[str, ...]) -> dict[str, str]:
    """`("Per=D", "Fill=P")` -> `{"Per": "D", "Fill": "P"}`.

    Spec §4.1 calls overrides "the mechanism by which the entire analytics
    library is exposed as data". An argument without an `=` is ignored
    rather than guessed at — inventing a key would run the model under an
    assumption nobody wrote.
    """
    overrides: dict[str, str] = {}
    for argument in arguments:
        text = str(argument).strip()
        if "=" in text:
            key, _, value = text.partition("=")
            overrides[key.strip()] = value.strip()
    return overrides


def _as_of(as_of: datetime | None) -> datetime:
    return as_of or datetime.now(UTC)


def _security(reference: str) -> SecurityQuery | None:
    parsed = parse_security(reference)
    if parsed is None:
        raise ValueError(f"{reference!r} is not a security reference")
    return parsed


def tdp(
    tapi: TapiView,
    reference: str,
    mnemonic: str,
    *overrides: str,
    as_of: datetime | None = None,
) -> object:
    """One current value — the spreadsheet's workhorse."""
    try:
        result = tapi.field(
            _security(reference), mnemonic, parse_overrides(overrides), as_of=_as_of(as_of)
        )
    except Exception as error:
        return f"#TREBLE: {error}"
    return BLANK if result.value is None else result.value


def tdh(
    tapi: TapiView,
    reference: str,
    mnemonic: str,
    start: str | date | None = None,
    end: str | date | None = None,
    *overrides: str,
    as_of: datetime | None = None,
) -> list[list[object]]:
    """A history, as a two-column grid of date and value.

    Returned as a grid rather than a flat list because that is what a
    spreadsheet spills into cells, and a caller must be able to see which
    date each value belongs to.
    """
    try:
        rows = tapi.series(_security(reference), mnemonic, as_of=_as_of(as_of))
    except Exception as error:
        return [[f"#TREBLE: {error}"]]

    window_start, window_end = _window(start), _window(end)
    grid: list[list[object]] = []
    for row in rows:
        when = str(row[0]) if row else ""
        if window_start and when < window_start:
            continue
        if window_end and when > window_end:
            continue
        grid.append([when, row[-1] if len(row) > 1 else None])
    # An empty result is one honest row, not zero: a formula that spills
    # nothing is indistinguishable from one that failed to calculate.
    return grid or [[BLANK, BLANK]]


def _window(bound: str | date | None) -> str | None:
    """Normalise a date bound to ISO for comparison against series keys."""
    if bound is None or bound == "":
        return None
    if isinstance(bound, datetime):
        return bound.date().isoformat()
    if isinstance(bound, date):
        return bound.isoformat()
    text = str(bound).strip()
    for form in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, form).date().isoformat()  # noqa: DTZ007
        except ValueError:
            continue
    return None


def tds(
    tapi: TapiView,
    reference: str,
    mnemonic: str,
    *overrides: str,
    as_of: datetime | None = None,
) -> list[list[object]]:
    """A bulk data set — index members, holders, capital structure (§4.1).

    Shares the series path with `TDH`; the difference is intent rather than
    mechanism, and the grid may be wider than two columns.
    """
    try:
        rows = tapi.series(_security(reference), mnemonic, as_of=_as_of(as_of))
    except Exception as error:
        return [[f"#TREBLE: {error}"]]
    return [list(row) for row in rows] or [[BLANK]]


def tql(tapi: TapiView, query: str, *, as_of: datetime | None = None) -> list[list[object]]:
    """A TQL query, spilled as a grid (§4.2).

    Routed through the same TAPI binding the `SRCH` and `EQS` screens use,
    so a query in a cell and a query on a screen cannot disagree.
    """
    from treble.tapi.local import TQL_BINDING

    try:
        rows = tapi.series(None, f"{TQL_BINDING}{query}", as_of=_as_of(as_of))
    except Exception as error:
        return [[f"#TREBLE: {error}"]]
    return [list(row) for row in rows] or [[BLANK]]
