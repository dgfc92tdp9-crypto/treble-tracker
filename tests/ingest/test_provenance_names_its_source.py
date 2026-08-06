"""Every adapter's provenance must name the adapter (I1).

Found on 2026-08-06 while auditing what each source publishes against what
gets read. Six of fifteen adapters stamped a `source_system` string that
differed from their own `meta.source_id`:

    ecb-fx            -> "ecb"
    edgar-companyfacts -> "edgar"
    edgar-submissions  -> "edgar"
    edgar-bulk         -> "edgar"
    treasury-auctions  -> "treasury-fiscaldata"
    trace-api          -> "finra-trace"

Three separate EDGAR adapters therefore collapsed to one name, so a value's
provenance could not say whether it came from companyfacts, from submissions
or from the bulk file — three different extraction paths with three different
parser versions. `SPTR` renders that chain, so the loss is visible to a user
asking where a number came from.

It also breaks the join a reader would naturally make. Joining the ingest log
to the facts it produced on the source name silently returns nothing for
those six, which reads as "this adapter has ingested nothing" rather than as
a naming mismatch. That happened twice while writing the audit.

An AST check rather than a runtime one, deliberately: this must hold for
every adapter including those with no fixture and those that cannot run
offline, and the property is syntactic — the literal must not be there.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest

import treble.ingest
from treble.ingest.base import SourceAdapter


def adapter_modules() -> list[str]:
    """Modules defining a `SourceAdapter`, discovered rather than listed."""
    found: list[str] = []
    for info in pkgutil.walk_packages(treble.ingest.__path__, prefix="treble.ingest."):
        module = importlib.import_module(info.name)
        for _, obj in vars(module).items():
            if (
                inspect.isclass(obj)
                and issubclass(obj, SourceAdapter)
                and obj is not SourceAdapter
                and obj.__module__ == info.name
            ):
                found.append(info.name)
                break
    return sorted(found)


def _provenance_source_arguments(module_name: str) -> list[ast.expr]:
    """Every `source_system=` argument in a `Provenance(...)` call."""
    path = Path(importlib.import_module(module_name).__file__ or "")
    tree = ast.parse(path.read_text())
    out: list[ast.expr] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name != "Provenance":
            continue
        out.extend(kw.value for kw in node.keywords if kw.arg == "source_system")
    return out


def test_some_adapters_were_found() -> None:
    """Guards the parametrised test below: an empty discovery would let it
    pass by checking nothing."""
    assert len(adapter_modules()) >= 8


@pytest.mark.parametrize("module_name", adapter_modules())
def test_provenance_source_system_is_the_adapters_own_id(module_name: str) -> None:
    """`source_system=self.meta.source_id`, never a literal.

    A literal is free to disagree with the id the adapter registers under,
    and did in six of fifteen adapters. Writing the id means the two cannot
    drift, and means renaming a source updates its provenance with it.
    """
    for argument in _provenance_source_arguments(module_name):
        assert not isinstance(argument, ast.Constant), (
            f"{module_name}: Provenance(source_system={argument.value!r}) is a literal. "
            "Use self.meta.source_id, or the provenance can name a source the adapter "
            "is not registered as — which is how three EDGAR adapters came to be "
            "indistinguishable in the provenance chain."
        )
        assert isinstance(argument, ast.Attribute) and argument.attr == "source_id", (
            f"{module_name}: source_system should be `self.meta.source_id`"
        )


def test_every_adapter_declares_a_distinct_source_id() -> None:
    """Two adapters sharing an id would make their facts indistinguishable in
    provenance even with the fix above."""
    ids: dict[str, str] = {}
    for module_name in adapter_modules():
        module = importlib.import_module(module_name)
        for name, obj in vars(module).items():
            if (
                inspect.isclass(obj)
                and issubclass(obj, SourceAdapter)
                and obj is not SourceAdapter
                and obj.__module__ == module_name
            ):
                source_id = obj.meta.source_id
                assert source_id not in ids, (
                    f"{name} and {ids[source_id]} both declare source_id {source_id!r}"
                )
                ids[source_id] = name
    assert len(ids) >= 8
