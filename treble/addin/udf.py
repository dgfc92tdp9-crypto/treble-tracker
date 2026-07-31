"""xlwings user-defined functions — the Excel shell (spec §4.1).

Deliberately thin. Every function here delegates to `functions`, which is
plain Python over a TAPI view and is where the tests live: a spreadsheet
function that can only be exercised inside Excel is one that does not get
exercised.

Import it into Excel with `xlwings addin install` and the workbook's
`TREBLE_*` UDF module setting, or run `treble addin` for the instructions.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import xlwings as xw

from treble.addin import functions


@lru_cache(maxsize=1)
def _tapi() -> Any:
    """The data path, built once per Excel session.

    Cached because a workbook may hold thousands of formulas and each would
    otherwise reopen the store and reload the ticker index.
    """
    from treble.cmd.cli import DEFAULT_DATA_DIR, _local_tapi
    from treble.cmd.env import load_env

    load_env(Path(DEFAULT_DATA_DIR).parent)
    return _local_tapi(DEFAULT_DATA_DIR, None)


@xw.func
@xw.arg("overrides", ndim=1)
def TDP(security: str, field: str, *overrides: str) -> object:  # noqa: N802
    """Current value of a field for a security."""
    return functions.tdp(_tapi(), security, field, *overrides)


@xw.func
@xw.ret(expand="table")
def TDH(  # noqa: N802
    security: str, field: str, start: str = "", end: str = "", *overrides: str
) -> list[list[object]]:
    """History of a field, as date and value."""
    return functions.tdh(_tapi(), security, field, start, end, *overrides)


@xw.func
@xw.ret(expand="table")
def TDS(security: str, field: str, *overrides: str) -> list[list[object]]:  # noqa: N802
    """A bulk data set for a security."""
    return functions.tds(_tapi(), security, field, *overrides)


@xw.func
@xw.ret(expand="table")
def TQL(query: str) -> list[list[object]]:  # noqa: N802
    """Run a TQL query and spill the result."""
    return functions.tql(_tapi(), query)
