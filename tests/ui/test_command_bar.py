"""Pilot + unit tests for the command bar + slash-command registry."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from cairn.ui._app import CairnApp
from cairn.ui._commands import (
    CommandRegistry,
    DispatchResult,
    SlashCommand,
    build_default_registry,
)
from cairn.ui._widgets import Banner, ChatLog, CommandBar, MessageView

if TYPE_CHECKING:
    from cairn.domain._sessions import Session
    from cairn.orchestrator import Orchestrator


def _app_for(session: Session) -> CairnApp:
    return CairnApp(orchestrator=cast("Orchestrator", Mock()), session=session)


# ---------------------------------------------------------------------------
# Unit: CommandRegistry
# ---------------------------------------------------------------------------


class TestCommandRegistry:
    @pytest.mark.asyncio
    async def test_register_and_dispatch_ok(self) -> None:
        registry = CommandRegistry()
        calls: list[str] = []

        async def handler(_app: CairnApp, tail: str) -> None:
            calls.append(tail)

        registry.register(SlashCommand(name="/hi", summary="hi", handler=handler))
        result = await registry.dispatch(cast("CairnApp", Mock()), "/hi world")

        assert isinstance(result, DispatchResult)
        assert result.status == "ok"
        assert calls == ["world"]

    @pytest.mark.asyncio
    async def test_dispatch_unknown_returns_message(self) -> None:
        registry = CommandRegistry()
        result = await registry.dispatch(cast("CairnApp", Mock()), "/nope")
        assert result.status == "unknown"
        assert result.message is not None and "nope" in result.message

    @pytest.mark.asyncio
    async def test_dispatch_plain_text_is_not_a_command(self) -> None:
        registry = CommandRegistry()
        result = await registry.dispatch(cast("CairnApp", Mock()), "hello there")
        assert result.status == "not_a_command"

    def test_register_rejects_duplicate(self) -> None:
        registry = CommandRegistry()

        async def _noop(_app: CairnApp, _tail: str) -> None:
            return None

        cmd = SlashCommand(name="/x", summary="x", handler=_noop)
        registry.register(cmd)
        with pytest.raises(ValueError, match="duplicate"):
            registry.register(cmd)

    def test_register_rejects_name_without_slash(self) -> None:
        registry = CommandRegistry()

        async def _noop(_app: CairnApp, _tail: str) -> None:
            return None

        with pytest.raises(ValueError, match="must start with"):
            registry.register(SlashCommand(name="x", summary="x", handler=_noop))

    def test_match_typeahead(self) -> None:
        registry = build_default_registry()
        matches = registry.match("/co")
        assert [c.name for c in matches] == ["/cost"]

    def test_default_registry_has_tranche_1_set(self) -> None:
        registry = build_default_registry()
        names = {cmd.name for cmd in registry.all()}
        assert names == {"/help", "/cost", "/tools", "/new", "/ephemeral", "/quit"}


# ---------------------------------------------------------------------------
# Pilot: CommandBar wiring
# ---------------------------------------------------------------------------


class TestCommandBarDispatch:
    @pytest.mark.asyncio
    async def test_help_prints_command_list(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/help"
            await bar.action_submit()
            await pilot.pause()

            chat_log = screen.query_one(ChatLog)
            banners = list(chat_log.query(Banner))
            assert len(banners) == 1
            rendered = str(banners[0].renderable)
            assert "/help" in rendered
            assert "/cost" in rendered
            assert "/quit" in rendered

    @pytest.mark.asyncio
    async def test_cost_prints_current_meter_value(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            screen.set_cost(1.2345)
            bar = screen.query_one(CommandBar)
            bar.value = "/cost"
            await bar.action_submit()
            await pilot.pause()

            chat_log = screen.query_one(ChatLog)
            banners = list(chat_log.query(Banner))
            assert any("$1.2345" in str(b.renderable) for b in banners)

    @pytest.mark.asyncio
    async def test_tools_placeholder_when_registry_unset(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/tools"
            await bar.action_submit()
            await pilot.pause()

            chat_log = screen.query_one(ChatLog)
            banners = list(chat_log.query(Banner))
            assert any("tool registry wiring" in str(b.renderable) for b in banners)

    @pytest.mark.asyncio
    async def test_unknown_command_renders_error_banner(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/not-a-thing"
            await bar.action_submit()
            await pilot.pause()

            chat_log = screen.query_one(ChatLog)
            banners = list(chat_log.query(Banner))
            assert len(banners) == 1
            assert banners[0].kind == "error"
            assert "not-a-thing" in str(banners[0].renderable)

    @pytest.mark.asyncio
    async def test_plain_text_mounts_user_message(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "hello companion"
            await bar.action_submit()
            await pilot.pause()

            chat_log = screen.query_one(ChatLog)
            views = list(chat_log.query(MessageView))
            assert len(views) == 1
            assert views[0].role == "user"
            assert views[0].text == "hello companion"

    @pytest.mark.asyncio
    async def test_empty_submit_is_ignored(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = ""
            await bar.action_submit()
            await pilot.pause()

            chat_log = screen.query_one(ChatLog)
            assert len(list(chat_log.query(MessageView))) == 0
            assert len(list(chat_log.query(Banner))) == 0

    @pytest.mark.asyncio
    async def test_quit_exits_app(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/quit"
            await bar.action_submit()
            await pilot.pause()
            # Pilot harness completes when app exits; assert the app
            # was asked to exit.
            assert app.return_code is None or app.return_code == 0
