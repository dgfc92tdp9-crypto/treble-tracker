"""`CNVS` — Canvas and FDC3 context propagation (spec §5.3).

    Selecting a security in a red-linked monitor updates every other
    red-linked component instantly.

Every way of getting that wrong is silent, which is why these tests are
mostly about who *does not* receive a broadcast. A component showing the
wrong instrument renders real data, correctly formatted, about something
the user did not ask for — there is nothing on screen to distinguish it
from a component that is right.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from treble.core.identifiers import SecurityQuery, YellowKey
from treble.render.canvas import (
    Canvas,
    CanvasComponent,
    Channel,
    Fdc3Instrument,
    Placement,
    UnknownComponentError,
)

IBM = Fdc3Instrument(ticker="IBM", name="International Business Machines")
AAPL = Fdc3Instrument(ticker="AAPL", name="Apple Inc")


def canvas() -> Canvas:
    """Two red, one blue, and *two* unlinked.

    Two unlinked components rather than one, deliberately. With a single
    one, "an unlinked component does not broadcast" passes for the wrong
    reason — the only other unlinked component is the sender itself, which
    is excluded anyway. A mutation removing the unlinked check survived
    that fixture, and the bug it would have hidden is real: unlinked
    components sharing an implicit group and syncing to each other, which
    is the exact opposite of what unlinking means.
    """
    return Canvas(
        [
            CanvasComponent(id="des", screen="DES", channel=Channel.RED),
            CanvasComponent(id="yas", screen="YAS", channel=Channel.RED),
            CanvasComponent(id="allq", screen="ALLQ", channel=Channel.BLUE),
            CanvasComponent(id="pinned", screen="GP", channel=None),
            CanvasComponent(id="pinned2", screen="HP", channel=None),
        ]
    )


class TestPropagationReachesTheGroup:
    def test_a_broadcast_updates_the_rest_of_the_channel(self) -> None:
        board = canvas()
        assert board.broadcast("des", IBM) == ("yas",)
        assert board.context_of("yas") == IBM

    def test_the_sender_holds_the_context_it_sent(self) -> None:
        board = canvas()
        board.broadcast("des", IBM)
        assert board.context_of("des") == IBM

    def test_the_sender_is_not_in_the_updated_list(self) -> None:
        """FDC3: a broadcaster does not receive its own broadcast. Echoing
        it back invites a loop between two components that each treat
        receipt as a selection."""
        assert "des" not in canvas().broadcast("des", IBM)

    def test_every_member_of_a_larger_group_receives(self) -> None:
        board = canvas()
        board.join("allq", Channel.RED)
        assert board.broadcast("des", IBM) == ("allq", "yas")


class TestPropagationStopsAtTheGroupBoundary:
    """The tests that matter. A leak here shows real data about the wrong
    instrument, and looks exactly like being right."""

    def test_another_colour_does_not_receive(self) -> None:
        board = canvas()
        board.broadcast("des", IBM)
        assert board.context_of("allq") is None

    def test_an_unlinked_component_does_not_receive(self) -> None:
        """A component the user deliberately pinned must not follow
        selections — that is the entire meaning of unlinking it."""
        board = canvas()
        board.broadcast("des", IBM)
        assert board.context_of("pinned") is None

    def test_two_groups_hold_different_contexts_at_once(self) -> None:
        board = canvas()
        board.broadcast("des", IBM)
        board.broadcast("allq", AAPL)
        assert board.context_of("yas") == IBM
        assert board.context_of("allq") == AAPL
        assert board.channel_context(Channel.RED) == IBM
        assert board.channel_context(Channel.BLUE) == AAPL

    def test_an_unlinked_sender_broadcasts_to_nobody(self) -> None:
        """Symmetry: unlinked has to mean unlinked for the sender too, or a
        pinned component silently drives the whole canvas."""
        board = canvas()
        assert board.broadcast("pinned", AAPL) == ()
        assert board.context_of("des") is None
        assert board.context_of("pinned") == AAPL

    def test_two_unlinked_components_do_not_sync_to_each_other(self) -> None:
        """Unlinked is not a group. Without an explicit check, "channel is
        None" matches every other unlinked component and they form an
        implicit shared channel — so pinning two components to different
        instruments would have them overwrite each other."""
        board = canvas()
        board.broadcast("pinned", AAPL)
        assert board.context_of("pinned2") is None
        board.broadcast("pinned2", IBM)
        assert board.context_of("pinned") == AAPL
        assert board.context_of("pinned2") == IBM


class TestJoiningSyncsImmediately:
    def test_a_joiner_receives_the_current_context(self) -> None:
        """The subtle one. A component that joined red and kept displaying
        its previous instrument would look linked and be stale, and on
        screen that is indistinguishable from being in sync."""
        board = canvas()
        board.broadcast("des", IBM)
        assert board.join("allq", Channel.RED) == IBM
        assert board.context_of("allq") == IBM

    def test_joining_an_empty_channel_returns_nothing_rather_than_guessing(self) -> None:
        board = canvas()
        assert board.join("pinned", Channel.GREEN) is None
        assert board.context_of("pinned") is None

    def test_a_component_added_to_a_live_channel_syncs_on_arrival(self) -> None:
        """Same reasoning as join: a component placed onto a canvas whose
        red group is already showing IBM must not start out blank while
        displaying a red link."""
        board = canvas()
        board.broadcast("des", IBM)
        board.add(CanvasComponent(id="late", screen="FA", channel=Channel.RED))
        assert board.context_of("late") == IBM

    def test_switching_channels_resyncs(self) -> None:
        board = canvas()
        board.broadcast("des", IBM)
        board.broadcast("allq", AAPL)
        board.join("yas", Channel.BLUE)
        assert board.context_of("yas") == AAPL


class TestLeaving:
    def test_leaving_keeps_the_last_context(self) -> None:
        """The component is displaying a real instrument the user chose.
        Blanking it on unlink would discard a valid view."""
        board = canvas()
        board.broadcast("des", IBM)
        board.leave("yas")
        assert board.context_of("yas") == IBM

    def test_leaving_stops_further_updates(self) -> None:
        """Which is what makes unlinking mean anything at all."""
        board = canvas()
        board.broadcast("des", IBM)
        board.leave("yas")
        assert board.broadcast("des", AAPL) == ()
        assert board.context_of("yas") == IBM

    def test_membership_reflects_the_change(self) -> None:
        board = canvas()
        assert board.members(Channel.RED) == ("des", "yas")
        board.leave("yas")
        assert board.members(Channel.RED) == ("des",)


class TestFdc3Interop:
    """Third-party applications join the same link groups (§5.3), so the
    context has to be the standard's, not ours."""

    def test_an_instrument_context_becomes_a_security_query(self) -> None:
        assert IBM.to_security_query() == SecurityQuery(ticker="IBM", key=YellowKey.EQUITY)

    def test_the_assumed_asset_class_is_a_parameter_not_a_constant(self) -> None:
        """FDC3 carries no yellow key. A third-party app broadcasting 'IBM'
        has not said whether it means the equity or a bond, so the
        assumption is stated at the call site — a canvas of corporate bond
        screens sets it once rather than silently mispricing every
        component."""
        query = IBM.to_security_query(key=YellowKey.CORP)
        assert query.key is YellowKey.CORP

    def test_an_identifier_only_context_still_resolves(self) -> None:
        """A CUSIP with no ticker is a normal FDC3 payload from a fixed
        income application."""
        context = Fdc3Instrument(cusip="912810UT3")
        assert context.to_security_query(key=YellowKey.GOVT).ticker == "912810UT3"

    def test_a_context_naming_nothing_is_refused(self) -> None:
        """Propagating it would blank every linked component, which reads as
        'this instrument has no data' rather than 'nothing was selected'."""
        with pytest.raises(ValueError, match="names nothing"):
            Fdc3Instrument(name="Some Company").to_security_query()

    def test_a_security_query_becomes_a_context(self) -> None:
        outbound = Fdc3Instrument.from_security_query(
            SecurityQuery(ticker="IBM", key=YellowKey.EQUITY)
        )
        assert outbound.ticker == "IBM"

    def test_the_round_trip_preserves_the_identifier(self) -> None:
        original = SecurityQuery(ticker="AAPL", key=YellowKey.EQUITY)
        assert Fdc3Instrument.from_security_query(original).to_security_query() == original


class TestRefusals:
    def test_an_unknown_component_is_refused_not_ignored(self) -> None:
        """A broadcast to a component that does not exist is a caller bug,
        and doing nothing would make it look like a link with no
        listeners."""
        with pytest.raises(UnknownComponentError, match="ghost"):
            canvas().broadcast("ghost", IBM)

    def test_joining_an_unknown_component_is_refused(self) -> None:
        with pytest.raises(UnknownComponentError):
            canvas().join("ghost", Channel.RED)

    def test_a_duplicate_component_id_is_refused(self) -> None:
        """Two components sharing an id means one of them silently receives
        the other's context."""
        board = canvas()
        with pytest.raises(ValueError, match="already holds"):
            board.add(CanvasComponent(id="des", screen="FA"))

    def test_every_channel_is_a_colour(self) -> None:
        """§5.3 links by colour group. A channel a user cannot see on screen
        is a link they cannot verify."""
        assert {c.value for c in Channel} == {
            "red",
            "blue",
            "green",
            "yellow",
            "orange",
            "purple",
        }


class TestLayout:
    """Placement in grid cells, not pixels (spec §5.3, §4.2)."""

    def test_a_layout_survives_a_round_trip(self) -> None:
        board = Canvas(
            [
                CanvasComponent(
                    id="des",
                    screen="DES",
                    channel=Channel.RED,
                    placement=Placement(x=0, y=0, width=40, height=20),
                ),
                CanvasComponent(
                    id="yas",
                    screen="YAS",
                    channel=Channel.RED,
                    placement=Placement(x=40, y=0, width=40, height=20, display=1),
                ),
            ]
        )
        restored = Canvas.from_json(board.to_json())
        assert restored.component("yas").placement == board.component("yas").placement
        assert restored.component("des").channel is Channel.RED

    def test_context_is_not_saved_with_the_layout(self) -> None:
        """Reopening a workspace tomorrow and finding yesterday's instrument
        on every screen, with nothing saying it is a day old, is the
        stale-display failure this project refuses everywhere else — and a
        layout file is a quiet place for it to happen."""
        board = canvas()
        board.broadcast("des", IBM)
        restored = Canvas.from_json(board.to_json())
        assert restored.context_of("des") is None
        assert restored.channel_context(Channel.RED) is None

    def test_links_are_saved_with_the_layout(self) -> None:
        """The links are structure, unlike the context. A restored canvas
        whose components had forgotten their colour groups would look
        arranged and behave unlinked."""
        restored = Canvas.from_json(canvas().to_json())
        assert restored.members(Channel.RED) == ("des", "yas")
        assert restored.component("pinned").channel is None

    def test_overlaps_are_reported_not_prevented(self) -> None:
        """§4.2's arbitrary window mode allows floating windows over one
        another, so this informs a renderer rather than refusing."""
        board = Canvas(
            [
                CanvasComponent(
                    id="a", screen="DES", placement=Placement(x=0, y=0, width=10, height=10)
                ),
                CanvasComponent(
                    id="b", screen="YAS", placement=Placement(x=5, y=5, width=10, height=10)
                ),
                CanvasComponent(
                    id="c", screen="GP", placement=Placement(x=50, y=50, width=10, height=10)
                ),
            ]
        )
        assert board.overlapping() == (("a", "b"),)

    def test_the_same_cells_on_different_displays_do_not_overlap(self) -> None:
        board = Canvas(
            [
                CanvasComponent(
                    id="a", screen="DES", placement=Placement(x=0, y=0, width=10, height=10)
                ),
                CanvasComponent(
                    id="b",
                    screen="YAS",
                    placement=Placement(x=0, y=0, width=10, height=10, display=1),
                ),
            ]
        )
        assert board.overlapping() == ()

    def test_restoring_onto_fewer_displays_is_refused(self, tmp_path: Path) -> None:
        """A layout built across three monitors, restored onto one, would
        place components off-screen. Silently reflowing would lose an
        arrangement the user built; that is the renderer's offer to make
        with the user watching, not the loader's to make quietly."""
        board = Canvas(
            [
                CanvasComponent(
                    id="a", screen="DES", placement=Placement(x=0, y=0, width=10, height=10)
                ),
                CanvasComponent(
                    id="b",
                    screen="YAS",
                    placement=Placement(x=0, y=0, width=10, height=10, display=2),
                ),
            ]
        )
        path = tmp_path / "layout.json"
        board.save(path)
        with pytest.raises(ValueError, match="only 1 are available"):
            Canvas.load(path, displays=1)
        assert Canvas.load(path, displays=3).displays() == (0, 2)

    def test_an_unversioned_layout_is_refused(self) -> None:
        """A layout half-understood places components wrongly and links them
        into the wrong groups, which reads as a working canvas."""
        with pytest.raises(ValueError, match="not supported"):
            Canvas.from_json('{"version": 99, "components": []}')

    def test_a_component_without_a_placement_is_valid(self) -> None:
        """Context propagation is meaningful without a layout — the TUI has
        no free-form placement and still participates in link groups."""
        board = Canvas([CanvasComponent(id="tui", screen="DES", channel=Channel.RED)])
        assert board.component("tui").placement is None
        assert board.displays() == ()
