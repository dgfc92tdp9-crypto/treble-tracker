"""`CNVS` — the Canvas workspace and FDC3 context propagation (spec §5.3).

    Selecting a security in a red-linked monitor updates every other
    red-linked component instantly.

That sentence is the whole feature, and every way of getting it wrong is
silent. A component that *looks* linked and is not keeps showing a
different security than the one the user just selected, with no error and no
visible difference from a component that is correctly in sync. A component
that receives context from the wrong group shows real data about the wrong
instrument, which is worse.

So the four properties below are enforced here and tested rather than
assumed:

1. **A broadcast reaches every component on the sender's channel** — and no
   others. Not the unlinked ones, not the other colours.
2. **Joining a channel syncs immediately.** FDC3 semantics, and the reason
   matters: a component that joined red and kept displaying its previous
   instrument would look linked and be stale. The current context arrives
   on join or the link is a lie.
3. **Leaving stops updates and keeps the last context.** The component is
   still showing something real, and it must stop tracking. Blanking it
   would discard a valid view; continuing to update it would make "unlink"
   meaningless.
4. **The sender does not receive its own broadcast**, per FDC3. It already
   has the context; echoing it back invites re-broadcast loops between two
   components that each treat receipt as a selection.

**Third-party applications join the same link groups** (§5.3). That is the
point of using FDC3 rather than an internal bus, and it has a consequence
this module has to handle honestly: an `fdc3.instrument` context carries
identifiers and *no asset class*. A third-party app broadcasting "IBM" does
not say whether it means the equity or a bond. See
:meth:`Fdc3Instrument.to_security_query`.

**Does the TUI participate?** The question was open while this was designed,
because FDC3 is a browser interop standard and an answer of "no" would have
been I6's first exception — one definition, many renderers, except this one.
The answer is that it splits, and the split is visible in the types rather
than in a caveat:

- *Context propagation is renderer-agnostic.* Everything above is ordinary
  Python with no browser dependency, so a TUI pane joins a colour group and
  receives selections exactly like a desktop component. I6 holds.
- *Free-form placement is not.* :class:`Placement` describes floating
  windows across displays (§4.2), and a terminal has one grid and no
  windows. So `CanvasComponent.placement` is **optional**, and a component
  without one is fully functional — linked, receiving context, simply not
  positioned.

That is why the layout is stored in grid cells rather than pixels: cells are
something both surfaces can mean.
"""

from __future__ import annotations

import enum
import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from treble.core.identifiers import SecurityQuery, YellowKey
from treble.render.contract.buffer import CellBuffer
from treble.render.contract.registry import get_screen
from treble.render.contract.resolver import ScreenContext, TapiView, resolve


class Channel(enum.Enum):
    """FDC3 user channels, which are colour-coded by the standard.

    Colours rather than numbers because that is what the user sees and what
    §5.3 describes: "links them by colour group". A channel identifier a
    user cannot see on screen is a link they cannot verify.
    """

    RED = "red"
    BLUE = "blue"
    GREEN = "green"
    YELLOW = "yellow"
    ORANGE = "orange"
    PURPLE = "purple"


class Fdc3Instrument(BaseModel):
    """An `fdc3.instrument` context, as it arrives on the wire.

    Field names follow the FDC3 standard's `id` object rather than this
    project's conventions, because the whole value of the standard is that
    an application which has never heard of Treble Tracker can broadcast one.
    """

    model_config = ConfigDict(frozen=True)

    ticker: str | None = None
    isin: str | None = None
    cusip: str | None = None
    figi: str | None = None
    name: str | None = None

    def to_security_query(self, *, key: YellowKey = YellowKey.EQUITY) -> SecurityQuery:
        """Resolve to a security reference, or refuse.

        **FDC3 carries no asset class.** `fdc3.instrument` has identifiers
        and a name; it has no yellow key, so a third-party application
        broadcasting "IBM" has not said whether it means the equity or one
        of the bonds. Equity is assumed because that is what `instrument`
        conventionally means in FDC3 — and the assumption is a named
        parameter rather than a buried constant, so a Canvas of corporate
        bond screens can set it once instead of silently mispricing every
        component.

        The safety net downstream is that `LocalTapi.resolve` refuses an
        identifier it does not hold, so a bond arriving as an equity fails
        loudly at resolution instead of rendering a screen of dashes.
        """
        identifier = self.ticker or self.cusip or self.isin or self.figi
        if not identifier:
            raise ValueError(
                "an fdc3.instrument context with no ticker, CUSIP, ISIN or FIGI names "
                "nothing; propagating it would blank every linked component"
            )
        return SecurityQuery(ticker=identifier, key=key)

    @classmethod
    def from_security_query(cls, security: SecurityQuery) -> Fdc3Instrument:
        """The outbound direction: what this workspace broadcasts."""
        return cls(ticker=security.ticker)


class Placement(BaseModel):
    """Where a component sits (spec §5.3, §4.2 arbitrary window mode).

    Grid cells, not pixels. A layout saved in pixels is a layout that only
    reconstructs on the display it was saved from — and §5.3 says layouts
    follow a user via Treble Anywhere, which means onto a different machine
    with different screens. Cells survive that; pixels do not.
    """

    model_config = ConfigDict(frozen=True)

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    #: Which display, for the multi-monitor case (§4.2). Zero-based, and
    #: kept even when a layout is restored onto a machine with fewer
    #: screens — see `Canvas.load`, which does not silently reflow.
    display: int = Field(default=0, ge=0)

    def overlaps(self, other: Placement) -> bool:
        """Whether two placements collide on the same display."""
        if self.display != other.display:
            return False
        return (
            self.x < other.x + other.width
            and other.x < self.x + self.width
            and self.y < other.y + other.height
            and other.y < self.y + self.height
        )


class CanvasComponent(BaseModel):
    """One placed component: a screen, where it sits, and the group it follows."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    #: Screen mnemonic — DES, YAS, ALLQ...
    screen: str
    #: The colour group, or None for an unlinked component. Unlinked is a
    #: real state, not a default to be filled in: a component the user has
    #: deliberately pinned to one instrument must not follow selections.
    channel: Channel | None = None
    #: Where it sits. Optional because context propagation is meaningful
    #: without a layout — the TUI has no free-form placement and still
    #: participates in link groups (see the module docstring).
    placement: Placement | None = None


class UnknownComponentError(KeyError):
    """A component id that is not on this canvas.

    Raised rather than ignored. A broadcast addressed to a component that
    does not exist is a bug in the caller, and silently doing nothing would
    make it look like a link that simply had no listeners.
    """


class Canvas:
    """A workspace of components, linked by colour group.

    Holds the per-channel context so that :meth:`join` can sync a component
    on arrival. Without that state a link is only correct for components
    that happened to be present when the last broadcast went out.
    """

    def __init__(self, components: list[CanvasComponent] | None = None) -> None:
        self._components: dict[str, CanvasComponent] = {}
        #: The current context per channel — what a component joining now
        #: should immediately display.
        self._channel_context: dict[Channel, Fdc3Instrument] = {}
        #: What each component is actually showing, which is not always the
        #: channel context: an unlinked component keeps the last context it
        #: received.
        self._component_context: dict[str, Fdc3Instrument] = {}
        for component in components or ():
            self.add(component)

    # -- composition ----------------------------------------------------

    def add(self, component: CanvasComponent) -> None:
        if component.id in self._components:
            raise ValueError(f"canvas already holds a component with id {component.id!r}")
        self._components[component.id] = component
        # Joining at construction syncs too, for the same reason join does.
        if component.channel is not None and component.channel in self._channel_context:
            self._component_context[component.id] = self._channel_context[component.channel]

    def component(self, component_id: str) -> CanvasComponent:
        try:
            return self._components[component_id]
        except KeyError:
            raise UnknownComponentError(
                f"no component {component_id!r} on this canvas; have: "
                + ", ".join(sorted(self._components))
            ) from None

    @property
    def component_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._components))

    def members(self, channel: Channel) -> tuple[str, ...]:
        return tuple(sorted(cid for cid, c in self._components.items() if c.channel is channel))

    # -- linking --------------------------------------------------------

    def join(self, component_id: str, channel: Channel) -> Fdc3Instrument | None:
        """Link a component to a colour group, and sync it immediately.

        Returns the context it now shows, or None if the channel has none
        yet. The sync is the point: a component that joined red and kept
        displaying its previous instrument would look linked and be stale,
        which is indistinguishable on screen from being correctly in sync.
        """
        existing = self.component(component_id)
        self._components[component_id] = existing.model_copy(update={"channel": channel})
        context = self._channel_context.get(channel)
        if context is not None:
            self._component_context[component_id] = context
        return context

    def leave(self, component_id: str) -> None:
        """Unlink, keeping whatever the component is currently showing.

        Not blanked: it is displaying a real instrument the user chose, and
        discarding that on unlink would lose a valid view. Not still
        tracking either, which is what makes unlink mean anything.
        """
        existing = self.component(component_id)
        self._components[component_id] = existing.model_copy(update={"channel": None})

    # -- context propagation --------------------------------------------

    def broadcast(self, component_id: str, context: Fdc3Instrument) -> tuple[str, ...]:
        """Publish a selection from one component. Returns who was updated.

        The sender is excluded, per FDC3: it already holds the context, and
        echoing it back invites a loop between two components that each
        treat receipt as a selection.

        An unlinked sender updates only itself — a deliberate no-op for the
        rest of the canvas rather than a silent broadcast to everyone, which
        is what "unlinked" has to mean for the sender as well as the
        receiver.
        """
        sender = self.component(component_id)
        self._component_context[component_id] = context
        if sender.channel is None:
            return ()

        self._channel_context[sender.channel] = context
        updated = [
            cid
            for cid, other in self._components.items()
            if other.channel is sender.channel and cid != component_id
        ]
        for cid in updated:
            self._component_context[cid] = context
        return tuple(sorted(updated))

    def context_of(self, component_id: str) -> Fdc3Instrument | None:
        """What a component is currently showing."""
        self.component(component_id)  # raises for an unknown id
        return self._component_context.get(component_id)

    def channel_context(self, channel: Channel) -> Fdc3Instrument | None:
        return self._channel_context.get(channel)

    # -- layout ---------------------------------------------------------

    def overlapping(self) -> tuple[tuple[str, str], ...]:
        """Pairs of components whose placements collide.

        Reported rather than prevented. Overlapping windows are a legitimate
        arrangement — §4.2's arbitrary window mode explicitly allows floating
        windows over one another — so this exists for a renderer that wants
        to warn or auto-arrange, not as a refusal.
        """
        placed = [(cid, c) for cid, c in sorted(self._components.items()) if c.placement]
        return tuple(
            (a_id, b_id)
            for i, (a_id, a) in enumerate(placed)
            for b_id, b in placed[i + 1 :]
            if a.placement is not None
            and b.placement is not None
            and a.placement.overlaps(b.placement)
        )

    def displays(self) -> tuple[int, ...]:
        """Displays this layout expects, in order."""
        return tuple(
            sorted({c.placement.display for c in self._components.values() if c.placement})
        )

    # -- persistence (spec §5.3: layouts follow a user) -----------------

    def to_json(self) -> str:
        """Serialise the layout. Context is deliberately not saved.

        A saved canvas restores *where things are and how they are linked*,
        not what they were showing. Reopening a workspace tomorrow and
        finding yesterday's instrument on every screen — with no indication
        it is a day old — is the stale-display failure this project refuses
        everywhere else, and a layout file is a particularly quiet place for
        it to happen.
        """
        return json.dumps(
            {
                "version": 1,
                "components": [
                    c.model_dump(mode="json") for _, c in sorted(self._components.items())
                ],
            },
            indent=2,
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, payload: str) -> Canvas:
        """Restore a layout, or refuse.

        A version mismatch is an error rather than a best-effort read: a
        layout half-understood puts components in the wrong places and links
        them into the wrong groups, which looks like a working canvas.
        """
        try:
            document = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError(f"not a canvas layout: {error}") from error
        version = document.get("version")
        if version != 1:
            raise ValueError(
                f"canvas layout version {version!r} is not supported by this build; "
                "restoring it partially would place components wrongly and link them "
                "into the wrong groups, which reads as a working canvas"
            )
        return cls([CanvasComponent.model_validate(c) for c in document.get("components", [])])

    def save(self, path: Path) -> None:
        path.write_text(self.to_json() + "\n")

    @classmethod
    def load(cls, path: Path, *, displays: int | None = None) -> Canvas:
        """Restore from disk, optionally checking the display count.

        `displays` is the number of screens actually available. A layout
        saved across three monitors and restored onto one would put
        components on displays that do not exist; rather than silently
        reflowing them — which loses an arrangement the user built — this
        refuses and says what is missing. Reflowing is a decision for the
        renderer, with the user watching.
        """
        canvas = cls.from_json(path.read_text())
        if displays is not None:
            expected = canvas.displays()
            missing = [d for d in expected if d >= displays]
            if missing:
                raise ValueError(
                    f"layout expects display(s) {missing} but only {displays} are "
                    "available; components would be placed off-screen. Reflowing is the "
                    "renderer's decision to offer, not this loader's to make silently"
                )
        return canvas


def resolve_canvas(
    canvas: Canvas,
    *,
    tapi: TapiView,
    as_of: datetime,
    key: YellowKey = YellowKey.EQUITY,
) -> dict[str, CellBuffer]:
    """Render every component of a canvas at one instant.

    One `as_of` for the whole workspace, deliberately. Resolving each
    component against its own clock would let two linked screens show the
    same instrument as of different moments — a discrepancy that reads as a
    data disagreement rather than a timing artefact, and that nothing on
    screen would explain.

    A component with no context yet resolves with no security, which is what
    "nothing selected" looks like. That is a different state from a screen
    whose security has no data, and the screens already distinguish the two.

    An unknown screen mnemonic raises rather than yielding an empty pane: a
    blank rectangle on a canvas is indistinguishable from a component whose
    instrument has nothing to report.
    """
    buffers: dict[str, CellBuffer] = {}
    for component_id in canvas.component_ids:
        component = canvas.component(component_id)
        context = canvas.context_of(component_id)
        buffers[component_id] = resolve(
            get_screen(component.screen),
            ScreenContext(security=context.to_security_query(key=key) if context else None),
            as_of=as_of,
            tapi=tapi,
        )
    return buffers


__all__ = [
    "Canvas",
    "CanvasComponent",
    "Channel",
    "Fdc3Instrument",
    "Placement",
    "UnknownComponentError",
    "resolve_canvas",
]
