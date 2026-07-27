"""Semantic attribute → terminal style (spec §6.3).

Screen definitions carry *semantic tokens* (`label`, `stale`,
`model_derived`), never colours. Themes map tokens to a renderer's own
styling, which is why the same definition can render in the TUI, the
desktop client and the browser without change (I6).

The default palette follows §6.3 exactly. A high-contrast / colour-blind-safe
palette is required by §6.3 too ("green/red replaced by blue/orange with
shape encoding") and is provided here so the semantics survive the swap —
that is the point of theming tokens rather than colours.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from treble.render.contract.schema import Attr


class Theme(BaseModel):
    """Token → Rich style string."""

    model_config = ConfigDict(frozen=True)

    name: str
    styles: dict[str, str]

    def style_for(self, attrs: tuple[Attr, ...]) -> str:
        """Combine the styles for a cell's attributes.

        Order matters: later attributes win on conflicting colour, so
        `stale` deliberately sits last — a value known not to be current
        must *look* stale whatever else it is (§6.3 makes this mandatory).
        """
        ordered = sorted(attrs, key=lambda a: a is Attr.STALE)
        parts = [self.styles[a.value] for a in ordered if a.value in self.styles]
        return " ".join(parts)


#: §6.3's palette. Amber labels, white inputs, green/red direction, cyan
#: links, yellow highlight, grey stale, dotted underline for model-derived.
DEFAULT_THEME = Theme(
    name="default",
    styles={
        Attr.LABEL.value: "#d78700",  # amber: labels, static reference data
        Attr.EDITABLE.value: "white on #1c1c1c",
        Attr.LINK.value: "#00afd7",  # cyan: drillable
        Attr.POSITIVE.value: "#00af5f",
        Attr.NEGATIVE.value: "#d70000",
        Attr.WARNING.value: "#d7d700",
        Attr.STALE.value: "#767676",  # grey: not current — mandatory (§6.3)
        Attr.MODEL_DERIVED.value: "underline",  # dotted underline (§5.4)
        Attr.BLINK.value: "blink",
        Attr.EMPHASIS.value: "bold",
    },
)

#: §6.3 requires a colour-blind-safe alternative in which "green/red
#: [are] replaced by blue/orange with shape encoding". Semantics are
#: preserved: the meaning lives in the token, not the colour.
HIGH_CONTRAST_THEME = Theme(
    name="high-contrast",
    styles={
        Attr.LABEL.value: "bold white",
        Attr.EDITABLE.value: "black on white",
        Attr.LINK.value: "bold #5fafff underline",
        Attr.POSITIVE.value: "#5fafff",  # blue, not green
        Attr.NEGATIVE.value: "#ff8700",  # orange, not red
        Attr.WARNING.value: "bold #ffff00",
        Attr.STALE.value: "dim",
        Attr.MODEL_DERIVED.value: "underline",
        Attr.BLINK.value: "reverse",
        Attr.EMPHASIS.value: "bold",
    },
)

THEMES: dict[str, Theme] = {t.name: t for t in (DEFAULT_THEME, HIGH_CONTRAST_THEME)}


def get_theme(name: str = "default") -> Theme:
    if name not in THEMES:
        raise KeyError(f"unknown theme {name!r}; available: {', '.join(sorted(THEMES))}")
    return THEMES[name]
