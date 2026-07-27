"""Screen schema, abstract layout tree, renderer conformance suite (I6). BUILD FIRST.

Implements specification section §6.1.
See docs/treble-tracker-spec.md and CLAUDE.md.
"""

from treble.render.contract.buffer import (
    CellBuffer,
    ResolvedCell,
    ResolvedPane,
    layout_tree,
    text_snapshot,
)
from treble.render.contract.resolver import (
    ScreenContext,
    TapiView,
    resolve,
)
from treble.render.contract.schema import (
    Attr,
    BoundCell,
    Cell,
    ConditionalAttr,
    InputCell,
    LinkCell,
    PaneRegion,
    PaneType,
    Pos,
    Predicate,
    Rect,
    ScreenDef,
    StaticCell,
    TabDef,
    load_screen,
)
from treble.tapi.types import FieldResult

__all__ = [
    "Attr",
    "BoundCell",
    "Cell",
    "CellBuffer",
    "ConditionalAttr",
    "FieldResult",
    "InputCell",
    "LinkCell",
    "PaneRegion",
    "PaneType",
    "Pos",
    "Predicate",
    "Rect",
    "ResolvedCell",
    "ResolvedPane",
    "ScreenContext",
    "ScreenDef",
    "StaticCell",
    "TabDef",
    "TapiView",
    "layout_tree",
    "load_screen",
    "resolve",
    "text_snapshot",
]
