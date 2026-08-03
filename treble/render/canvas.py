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
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict, Field

from treble.core.identifiers import SecurityQuery, YellowKey


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


class CanvasComponent(BaseModel):
    """One placed component: a screen, and the group it follows."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    #: Screen mnemonic — DES, YAS, ALLQ...
    screen: str
    #: The colour group, or None for an unlinked component. Unlinked is a
    #: real state, not a default to be filled in: a component the user has
    #: deliberately pinned to one instrument must not follow selections.
    channel: Channel | None = None


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


__all__ = [
    "Canvas",
    "CanvasComponent",
    "Channel",
    "Fdc3Instrument",
    "UnknownComponentError",
]
