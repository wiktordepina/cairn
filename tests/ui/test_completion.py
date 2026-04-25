"""Pilot + unit tests for the slash-command completion menu."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from cairn.ui._app import CairnApp
from cairn.ui._commands import CommandRegistry, SlashCommand, build_default_registry
from cairn.ui._widgets import CommandBar, CompletionMenu

if TYPE_CHECKING:
    from cairn.domain._sessions import Session
    from cairn.orchestrator import Orchestrator


def _app_for(session: Session) -> CairnApp:
    return CairnApp(orchestrator=cast("Orchestrator", Mock()), session=session)


# ---------------------------------------------------------------------------
# Unit: CompletionMenu.sync
# ---------------------------------------------------------------------------


class TestCompletionMenuSync:
    @pytest.mark.asyncio
    async def test_opens_on_slash_prefix(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu = app.query_one(CompletionMenu)
            menu.sync("/c", build_default_registry())
            await pilot.pause()

            assert menu.is_open is True
            names = [cmd.name for cmd in menu.matches]
            assert names == ["/context", "/conventions", "/cost"]
            assert menu.selected_name == "/context"
            assert menu.has_class("-visible")

    @pytest.mark.asyncio
    async def test_empty_input_closes(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu = app.query_one(CompletionMenu)
            menu.sync("/c", build_default_registry())
            menu.sync("", build_default_registry())
            await pilot.pause()

            assert menu.is_open is False
            assert menu.selected_name is None
            assert not menu.has_class("-visible")

    @pytest.mark.asyncio
    async def test_plain_text_closes(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu = app.query_one(CompletionMenu)
            menu.sync("hello there", build_default_registry())
            await pilot.pause()

            assert menu.is_open is False

    @pytest.mark.asyncio
    async def test_whitespace_after_command_closes(self, companion_session: Session) -> None:
        """Once the user types a space the menu should close — they're
        now typing arguments, not picking a command."""
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu = app.query_one(CompletionMenu)
            menu.sync("/cost ", build_default_registry())
            await pilot.pause()

            assert menu.is_open is False

    @pytest.mark.asyncio
    async def test_no_matches_closes(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu = app.query_one(CompletionMenu)
            menu.sync("/zzz", build_default_registry())
            await pilot.pause()

            assert menu.is_open is False

    @pytest.mark.asyncio
    async def test_bare_slash_shows_all_commands(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu = app.query_one(CompletionMenu)
            menu.sync("/", build_default_registry())
            await pilot.pause()

            assert menu.is_open is True
            names = [cmd.name for cmd in menu.matches]
            assert names == [
                "/context",
                "/conventions",
                "/cost",
                "/ephemeral",
                "/help",
                "/model",
                "/new",
                "/persona",
                "/profile",
                "/quit",
                "/tools",
            ]


# ---------------------------------------------------------------------------
# Pilot: CommandBar ↔ CompletionMenu integration
# ---------------------------------------------------------------------------


class TestCommandBarKeyIntegration:
    @pytest.mark.asyncio
    async def test_typing_slash_opens_menu(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("/")
            await pilot.pause()

            menu = app.query_one(CompletionMenu)
            assert menu.is_open is True
            # Every tranche-1 command starts with '/'.
            names = [cmd.name for cmd in menu.matches]
            assert "/cost" in names
            assert "/help" in names

    @pytest.mark.asyncio
    async def test_down_arrow_moves_selection(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("/")
            await pilot.pause()

            menu = app.query_one(CompletionMenu)
            first = menu.selected_name
            await pilot.press("down")
            await pilot.pause()
            second = menu.selected_name

            assert first is not None
            assert second is not None
            assert first != second

    @pytest.mark.asyncio
    async def test_up_arrow_moves_selection_backwards(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("/")
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            menu = app.query_one(CompletionMenu)
            after_down = menu.selected_name
            await pilot.press("up")
            await pilot.pause()
            after_up = menu.selected_name

            assert after_down != after_up

    @pytest.mark.asyncio
    async def test_tab_completes_selection(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            # '/co' still matches both /context and /cost; press 's'
            # to narrow to the single /cost entry.
            await pilot.press("/")
            await pilot.press("c")
            await pilot.press("o")
            await pilot.press("s")
            await pilot.pause()

            menu = app.query_one(CompletionMenu)
            assert [cmd.name for cmd in menu.matches] == ["/cost"]

            await pilot.press("tab")
            await pilot.pause()

            bar = app.query_one(CommandBar)
            assert bar.value == "/cost "
            # Completion closes — a trailing space implies arguments.
            assert menu.is_open is False

    @pytest.mark.asyncio
    async def test_enter_executes_highlighted_command(self, companion_session: Session) -> None:
        """Enter while the menu is open swaps the typed prefix for the
        highlighted command and submits — one keystroke runs it.
        """
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            # Type '/co' — both /context and /cost match; /context is
            # highlighted (alphabetical).
            await pilot.press("/")
            await pilot.press("c")
            await pilot.press("o")
            await pilot.pause()

            menu = app.query_one(CompletionMenu)
            assert menu.is_open is True
            assert menu.selected_name == "/context"

            await pilot.press("enter")
            await pilot.pause()

            # The session screen ran /context, which (without a
            # context_source wired) emits a muted banner. Verify the
            # banner appeared, the input cleared, and the menu closed.
            from cairn.ui._widgets import Banner, ChatLog

            chat_log = screen.query_one(ChatLog)
            banners = list(chat_log.query(Banner))
            assert len(banners) == 1
            assert "/context" in str(banners[0].renderable)

            bar = app.query_one(CommandBar)
            assert bar.value == ""
            assert menu.is_open is False

    @pytest.mark.asyncio
    async def test_enter_without_selection_falls_through(self, companion_session: Session) -> None:
        """If the menu has no highlighted row, Enter falls through to
        Input's default submit path (operating on the typed value).
        """
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            bar = app.query_one(CommandBar)
            bar.value = "/c"
            await pilot.pause()
            menu = app.query_one(CompletionMenu)
            menu.highlighted = None  # force the no-selection state

            await pilot.press("enter")
            await pilot.pause()

            # '/c' is unknown — we expect the unknown-command error
            # banner from the default submit path.
            from cairn.ui._widgets import Banner, ChatLog

            screen = app.current_session_screen
            assert screen is not None
            chat_log = screen.query_one(ChatLog)
            banners = list(chat_log.query(Banner))
            assert any(b.kind == "error" for b in banners)

    @pytest.mark.asyncio
    async def test_escape_closes_menu(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("/")
            await pilot.pause()

            menu = app.query_one(CompletionMenu)
            assert menu.is_open is True

            await pilot.press("escape")
            await pilot.pause()

            assert menu.is_open is False
            # Bar preserves the typed text — Esc dismisses the menu,
            # not the input.
            bar = app.query_one(CommandBar)
            assert bar.value == "/"

    @pytest.mark.asyncio
    async def test_tab_without_menu_is_noop(self, companion_session: Session) -> None:
        """Tab while the menu is closed must not fire completion — it
        should fall through to Textual's default focus-cycling, and
        the command bar value must not be mutated."""
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            bar = app.query_one(CommandBar)
            bar.value = "hello"
            await pilot.pause()
            await pilot.press("tab")
            await pilot.pause()

            assert bar.value == "hello"

    @pytest.mark.asyncio
    async def test_submit_closes_menu(self, companion_session: Session) -> None:
        """After submitting a command, the menu must hide — otherwise
        the popover lingers above the cleared input."""
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            bar = app.query_one(CommandBar)
            bar.value = "/cost"
            await pilot.pause()
            menu = app.query_one(CompletionMenu)
            assert menu.is_open is True

            await bar.action_submit()
            await pilot.pause()

            assert menu.is_open is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestCompletionMenuEdgeCases:
    @pytest.mark.asyncio
    async def test_tab_without_selection_is_noop(self, companion_session: Session) -> None:
        """If the menu is open but nothing is highlighted, Tab should
        not mutate the input. Constructed by using a registry where
        sync can't pick anything — an empty filtered set closes the
        menu, so we reach this state by emptying matches manually."""
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            bar = app.query_one(CommandBar)
            bar.value = "/c"
            await pilot.pause()
            menu = app.query_one(CompletionMenu)
            # Force the "no selection" state.
            menu.highlighted = None

            await pilot.press("tab")
            await pilot.pause()

            assert bar.value == "/c"

    @pytest.mark.asyncio
    async def test_menu_not_wired_does_not_crash(self, companion_session: Session) -> None:
        """A CommandBar constructed without a completion menu must
        still accept keys normally — the wiring is optional."""
        bar = CommandBar()  # no completion
        # The bar hasn't been mounted; just verify construction + a
        # direct call to on_key with no menu attached doesn't raise.
        from textual import events

        await bar.on_key(events.Key(key="down", character=None))


class TestDynamicRegistry:
    @pytest.mark.asyncio
    async def test_custom_command_appears_in_menu(self, companion_session: Session) -> None:
        """The registry is read at `sync()` time, so commands
        registered after startup (future `/persona` etc.) show up."""
        registry = CommandRegistry()

        async def _noop(_app: CairnApp, _tail: str) -> None:
            return None

        registry.register(SlashCommand(name="/aardvark", summary="ark", handler=_noop))

        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu = app.query_one(CompletionMenu)
            menu.sync("/a", registry)
            await pilot.pause()

            assert [cmd.name for cmd in menu.matches] == ["/aardvark"]
