"""The conformance suite itself (I6). Every case, every renderer."""

import json
import os

import pytest

from tests.conformance.framework import (
    CANVAS_RENDERERS,
    RENDERERS,
    CanvasCase,
    Case,
    canvas_reference_renderer,
    discover_canvas_cases,
    discover_cases,
    reference_renderer,
)

CASES = discover_cases()


def _regen_enabled() -> bool:
    return os.environ.get("TREBLE_REGEN_GOLDEN") == "1"


@pytest.mark.conformance
@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
class TestConformance:
    def test_goldens_exist(self, case: Case) -> None:
        if _regen_enabled():
            layout, text = reference_renderer(case)
            case.golden_layout.write_text(layout)
            case.golden_text.write_text(text)
        assert case.golden_layout.exists(), (
            f"{case.name}: golden missing — run with TREBLE_REGEN_GOLDEN=1 and review the diff"
        )
        assert case.golden_text.exists()

    @pytest.mark.parametrize("renderer_name", list(RENDERERS))
    def test_renderer_matches_goldens(self, case: Case, renderer_name: str) -> None:
        if not case.golden_layout.exists():
            pytest.skip("goldens not generated yet")
        layout, text = RENDERERS[renderer_name](case)
        golden_layout = case.golden_layout.read_text()
        # JSON equality is structural. A renderer in another language must
        # not be failed for how its runtime spells a number — Node emits 1
        # where CPython emits 1.0 for the same value. Everything that
        # carries meaning (positions, text, attributes, pane regions and
        # bindings) is still compared exactly, and the text snapshot below
        # — the thing a user actually sees — stays character-for-character.
        if json.loads(layout) != json.loads(golden_layout):
            assert layout == golden_layout, (
                f"{case.name}/{renderer_name}: layout tree diverges from golden"
            )
        assert text == case.golden_text.read_text(), (
            f"{case.name}/{renderer_name}: text snapshot diverges from golden"
        )


@pytest.mark.conformance
def test_at_least_one_case_exists() -> None:
    assert CASES, "conformance suite must not be empty"


CANVAS_CASES = discover_canvas_cases()


@pytest.mark.conformance
@pytest.mark.parametrize("case", CANVAS_CASES, ids=[c.name for c in CANVAS_CASES])
class TestCanvasConformance:
    """I6 for a whole workspace (§5.3).

    The single-screen suite above proves each component renders identically
    everywhere. This proves they are *placed* identically — which is a
    separate failure: a renderer that composed the same correct screens into
    the wrong arrangement, or dropped one, would pass every test above.
    """

    def test_goldens_exist(self, case: CanvasCase) -> None:
        if _regen_enabled():
            layout, text = canvas_reference_renderer(case)
            case.golden_layout.write_text(layout)
            case.golden_text.write_text(text)
        assert case.golden_layout.exists(), (
            f"{case.name}: golden missing — run with TREBLE_REGEN_GOLDEN=1 and review the diff"
        )
        assert case.golden_text.exists()

    @pytest.mark.parametrize("renderer_name", list(CANVAS_RENDERERS))
    def test_renderer_matches_goldens(self, case: CanvasCase, renderer_name: str) -> None:
        if not case.golden_layout.exists():
            pytest.skip("goldens not generated yet")
        layout, text = CANVAS_RENDERERS[renderer_name](case)
        if json.loads(layout) != json.loads(case.golden_layout.read_text()):
            assert layout == case.golden_layout.read_text(), (
                f"{case.name}/{renderer_name}: canvas layout tree diverges from golden"
            )
        assert text == case.golden_text.read_text(), (
            f"{case.name}/{renderer_name}: canvas text snapshot diverges from golden"
        )


@pytest.mark.conformance
def test_a_canvas_case_exists() -> None:
    """`CNVS` is a shipped screen, so it needs a case like any other — and
    it is the only one whose renderer draws more than one buffer."""
    assert CANVAS_CASES, "the canvas has no conformance case; CNVS is untested across renderers"


@pytest.mark.conformance
def test_every_shipped_screen_has_a_case() -> None:
    """I6's kill-test at the suite level.

    A screen with no conformance case is a screen no renderer is checked
    against — it can render differently on the TUI and the desktop, or
    render nothing at all, and nothing would fail.
    """
    from treble.render.contract.registry import available

    covered = {c.definition.mnemonic for c in CASES}
    missing = sorted(set(available()) - covered)
    assert not missing, f"screens with no conformance case: {', '.join(missing)}"


def test_every_shipped_tab_has_a_case() -> None:
    """The same kill-test, one level deeper — where it should always have been.

    The check above is satisfied by a single case on a four-tab screen, and
    a tab is not a variation on a layout: it is its own grid with its own
    binding, rendered by its own code path. An uncovered tab can render
    differently on the TUI and the desktop, or render nothing at all, and
    the per-screen check passes because a *sibling* tab has a case.

    Found by adding TVAL's snapshots tab and watching the suite stay green
    with nothing checking it. Every tab that existed before then already
    had one, which is why the gap survived: the convention was right and
    only the enforcement was shallow, so there was no failing example to
    notice.
    """
    from treble.render.contract.registry import available, get_screen

    # A case with no explicit tab resolves to the screen's first, so that
    # is the tab it covers. Treating it as covering nothing would demand a
    # redundant case for every single-tab screen.
    covered = {(c.definition.mnemonic, c.context.tab or c.definition.tabs[0].name) for c in CASES}
    missing = sorted(
        f"{mnemonic}:{tab.name}"
        for mnemonic in available()
        for tab in get_screen(mnemonic).tabs
        if (mnemonic, tab.name) not in covered
    )
    assert not missing, f"tabs with no conformance case: {', '.join(missing)}"
