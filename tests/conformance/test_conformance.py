"""The conformance suite itself (I6). Every case, every renderer."""

import json
import os

import pytest

from tests.conformance.framework import RENDERERS, Case, discover_cases, reference_renderer

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
