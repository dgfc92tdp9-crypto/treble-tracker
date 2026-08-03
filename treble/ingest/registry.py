"""Every shipped source and its declared terms (spec §9.3, §22.1).

`SourceMeta.redistribution_restricted` has been set on adapters since Phase 1
and, until this module, was read by nothing. Its own docstring said it
"drives the bulk-export guard"; there was no bulk-export guard. That is the
fourth mechanism in this project found declared and switched off — after
three adapters that had never run, an import contract with no test
protecting its own config, and a drift check whose failing cases had been
deleted rather than fixed.

So the registry is **discovered, not listed**. A hand-maintained list of
restricted sources is the same defect in a new place: an adapter added
without an entry would export freely and nothing would say so. Walking the
package means a source is in the registry because it exists, and the only
way to leave it out is to delete it.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import treble.ingest
from treble.ingest.base import SourceAdapter, SourceMeta


def all_sources() -> dict[str, SourceMeta]:
    """Every `SourceAdapter` subclass's declared metadata, by source id.

    Import-time discovery, like the analytics model registry: reading a
    registry that nothing had populated would report "no restricted
    sources" and read exactly like a clean bill of health.
    """
    sources: dict[str, SourceMeta] = {}
    for info in pkgutil.walk_packages(treble.ingest.__path__, prefix="treble.ingest."):
        module = importlib.import_module(info.name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, SourceAdapter) or obj is SourceAdapter:
                continue
            meta = getattr(obj, "meta", None)
            if not isinstance(meta, SourceMeta):
                continue
            existing = sources.get(meta.source_id)
            if existing is not None and existing != meta:
                raise ValueError(
                    f"two adapters declare source id {meta.source_id!r} with different "
                    "terms; the guard would apply whichever was imported last"
                )
            sources[meta.source_id] = meta
    return sources


def restricted_source_ids() -> frozenset[str]:
    """Sources whose terms forbid or do not permit redistribution.

    Includes sources whose terms could not be *verified*, not only those
    known to forbid it. `dtcc-sdr` is the live case: its terms sit behind
    bot protection and were never read, and DTCC sells a paid systematic-
    access product for the same data. Treating unverified as unrestricted
    would put the least-understood data in the first bulk export.
    """
    return frozenset(
        source_id for source_id, meta in all_sources().items() if meta.redistribution_restricted
    )


__all__ = ["all_sources", "restricted_source_ids"]
