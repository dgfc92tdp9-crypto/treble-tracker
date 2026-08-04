"""Conformance harness (I6, CLAUDE.md §4).

Each case directory under ``cases/`` holds:

- ``screen.yaml``   — the definition under test
- ``context.json``  — frozen ScreenContext + as_of
- ``tapi.json``     — frozen TAPI responses (fields and series)
- ``golden.layout.json`` / ``golden.txt`` — the two golden artefacts

Every registered renderer must reproduce both goldens from the same
definition + frozen inputs. A renderer that cannot express a definition
fails; it does not get a special case. Set ``TREBLE_REGEN_GOLDEN=1`` to
(re)write goldens from the reference resolver — review the diff like code.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from treble.core.identifiers import SecurityQuery
from treble.core.provenance import ProvenanceId
from treble.render.canvas import (
    Canvas,
    CanvasComponent,
    Channel,
    Placement,
    canvas_layout_tree,
    canvas_text_snapshot,
)
from treble.render.contract.buffer import (
    CellBuffer,
    canonical_json,
    layout_tree,
    text_snapshot,
)
from treble.render.contract.registry import get_screen
from treble.render.contract.resolver import FieldResult, ScreenContext, resolve
from treble.render.contract.schema import ScreenDef, load_screen

CASES_DIR = Path(__file__).parent / "cases"


class FrozenTapi:
    """A TapiView backed entirely by the case's recorded responses."""

    def __init__(self, recorded: dict[str, object]) -> None:
        fields = recorded.get("fields", {})
        assert isinstance(fields, dict)
        self._fields: dict[str, dict[str, object]] = fields
        series = recorded.get("series", {})
        assert isinstance(series, dict)
        self._series: dict[str, list[list[str | float | int | None]]] = series

    @staticmethod
    def _key(mnemonic: str, overrides: dict[str, str]) -> str:
        if not overrides:
            return mnemonic
        suffix = ",".join(f"{k}={v}" for k, v in sorted(overrides.items()))
        return f"{mnemonic}({suffix})"

    def field(
        self,
        security: SecurityQuery | None,
        mnemonic: str,
        overrides: dict[str, str],
        *,
        as_of: datetime,
    ) -> FieldResult:
        key = self._key(mnemonic, overrides)
        if key not in self._fields:
            raise KeyError(f"conformance case does not record field {key!r}")
        raw = dict(self._fields[key])
        pid = raw.pop("provenance_id", None)
        return FieldResult(
            provenance_id=ProvenanceId(str(pid)) if pid is not None else None,
            **raw,  # type: ignore[arg-type]
        )

    def series(
        self, security: SecurityQuery | None, binding: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        if binding not in self._series:
            raise KeyError(f"conformance case does not record series {binding!r}")
        return tuple(tuple(row) for row in self._series[binding])


class Case:
    definition: ScreenDef

    def __init__(self, path: Path) -> None:
        self.path = path
        self.name = path.name
        raw_context = json.loads((path / "context.json").read_text())
        # A case either carries its own definition (synthetic cases, which
        # exercise the contract itself) or names a shipped screen. Real
        # screens are referenced, never copied: a copied definition would
        # let the case keep passing against a screen that had changed
        # underneath it, which is the one thing conformance must not do.
        shipped = raw_context.pop("screen", None)
        if shipped is not None:
            self.definition = get_screen(shipped)
        else:
            self.definition = load_screen((path / "screen.yaml").read_text())
        self.as_of: datetime = datetime.fromisoformat(raw_context.pop("as_of"))
        if self.as_of.tzinfo is None:
            raise ValueError(f"{self.name}: as_of must be timezone-aware")
        security = raw_context.pop("security", None)
        self.context = ScreenContext(
            security=SecurityQuery.model_validate(security) if security else None,
            **raw_context,
        )
        self.tapi = FrozenTapi(json.loads((path / "tapi.json").read_text()))

    def reference_buffer(self) -> CellBuffer:
        return resolve(self.definition, self.context, as_of=self.as_of, tapi=self.tapi)

    @property
    def golden_layout(self) -> Path:
        return self.path / "golden.layout.json"

    @property
    def golden_text(self) -> Path:
        return self.path / "golden.txt"


def discover_cases() -> list[Case]:
    return [Case(p) for p in sorted(CASES_DIR.iterdir()) if p.is_dir()]


# A renderer under test maps the case to the two artefacts. The reference
# implementation resolves + projects; real renderers (TUI, web) must produce
# identical artefacts from their own rendering pipeline.
RendererUnderTest = Callable[[Case], tuple[str, str]]


def reference_renderer(case: Case) -> tuple[str, str]:
    buffer = case.reference_buffer()
    return layout_tree(buffer), text_snapshot(buffer)


def tui_renderer(case: Case) -> tuple[str, str]:
    """The Textual/Rich renderer's own pipeline — not a second call to the
    reference projection. If the TUI's styling, pane drawing or grid
    composition diverged, this is what would catch it."""
    from treble.render.tui.renderer import conformance_artifacts

    return conformance_artifacts(case.reference_buffer())


WEB_DIR = Path(__file__).parents[2] / "treble" / "render" / "web"


def web_renderer(case: Case) -> tuple[str, str]:
    """The TypeScript renderer the desktop shell and browser client share.

    Driven exactly as the desktop client is: given the layout-tree JSON that
    ``POST /command`` returns, and asked to render. Nothing about the screen
    definition reaches it -- which is the point of I6.

    A missing toolchain is a hard failure, not a skip. A renderer that is
    silently untested is worse than one that is absent, and this is the only
    check standing between the desktop client and a screen it draws wrongly.
    """
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node is required to run the web renderer's conformance")
    if not (WEB_DIR / "dist" / "renderer.js").exists():
        raise RuntimeError(f"web renderer is not built; run `make web` ({WEB_DIR}/dist missing)")

    buffer = case.reference_buffer()
    proc = subprocess.run(  # noqa: S603
        [node, str(WEB_DIR / "conformance.mjs")],
        input=layout_tree(buffer),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"web renderer failed: {proc.stderr.strip()}")
    result = json.loads(proc.stdout)
    return canonical_json(result["tree"]), result["text"]


RENDERERS: dict[str, RendererUnderTest] = {
    "reference": reference_renderer,
    "tui": tui_renderer,
    "web": web_renderer,
}


# ---------------------------------------------------------------------
# CNVS — whole-workspace conformance (spec §5.3)
# ---------------------------------------------------------------------

CANVAS_CASES_DIR = Path(__file__).parent / "canvas_cases"


class CanvasCase:
    """A workspace assembled from screen cases that are already under test.

    Components reference existing case directories rather than carrying
    their own definitions and frozen TAPI responses. That is deliberate: a
    canvas case built from its own inputs would re-test resolution, and a
    compositing bug would be indistinguishable from a resolver bug. Reusing
    cases whose single-screen artefacts are already golden leaves exactly
    one thing under test here — placing them.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.name = path.name
        raw = json.loads((path / "canvas.json").read_text())
        self.display: int = raw.get("display", 0)
        self.sources: dict[str, Case] = {}
        components = []
        for entry in raw["components"]:
            source = Case(CASES_DIR / entry["case"])
            mnemonic = source.definition.mnemonic
            if entry["screen"] != mnemonic:
                raise ValueError(
                    f"{self.name}: component {entry['id']!r} is labelled {entry['screen']!r} "
                    f"but case {entry['case']!r} renders {mnemonic!r}. A frame naming one "
                    "screen while showing another is the mislabelling this suite exists to stop"
                )
            self.sources[entry["id"]] = source
            components.append(
                CanvasComponent(
                    id=entry["id"],
                    screen=entry["screen"],
                    channel=Channel(entry["channel"]) if entry.get("channel") else None,
                    placement=(
                        Placement.model_validate(entry["placement"])
                        if entry.get("placement")
                        else None
                    ),
                )
            )
        self.canvas = Canvas(components)

    def buffers(self) -> dict[str, CellBuffer]:
        return {cid: case.reference_buffer() for cid, case in self.sources.items()}

    @property
    def golden_layout(self) -> Path:
        return self.path / "golden.layout.json"

    @property
    def golden_text(self) -> Path:
        return self.path / "golden.txt"


def discover_canvas_cases() -> list[CanvasCase]:
    if not CANVAS_CASES_DIR.exists():
        return []
    return [CanvasCase(p) for p in sorted(CANVAS_CASES_DIR.iterdir()) if p.is_dir()]


CanvasRendererUnderTest = Callable[[CanvasCase], tuple[str, str]]


def canvas_reference_renderer(case: CanvasCase) -> tuple[str, str]:
    buffers = case.buffers()
    return (
        canvas_layout_tree(case.canvas, buffers),
        canvas_text_snapshot(case.canvas, buffers, display=case.display),
    )


def canvas_tui_renderer(case: CanvasCase) -> tuple[str, str]:
    """The TUI's own compositor, styling stripped — not a second call to the
    shared projection."""
    from treble.render.tui.renderer import canvas_conformance_artifacts

    return canvas_conformance_artifacts(case.canvas, case.buffers(), display=case.display)


def canvas_web_renderer(case: CanvasCase) -> tuple[str, str]:
    """The TypeScript compositor the desktop shell and browser client share,
    driven with the canvas payload ``POST /command`` returns for `CNVS`."""
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node is required to run the web renderer's conformance")
    if not (WEB_DIR / "dist" / "renderer.js").exists():
        raise RuntimeError(f"web renderer is not built; run `make web` ({WEB_DIR}/dist missing)")

    proc = subprocess.run(  # noqa: S603
        [node, str(WEB_DIR / "conformance.mjs"), "canvas"],
        input=canvas_layout_tree(case.canvas, case.buffers()),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"web canvas renderer failed: {proc.stderr.strip()}")
    result = json.loads(proc.stdout)
    return canonical_json(result["tree"]), result["text"]


CANVAS_RENDERERS: dict[str, CanvasRendererUnderTest] = {
    "reference": canvas_reference_renderer,
    "tui": canvas_tui_renderer,
    "web": canvas_web_renderer,
}
