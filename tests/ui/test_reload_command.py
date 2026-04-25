"""Pilot tests for the `/reload` slash command."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from cairn.ui._app import CairnApp
from cairn.ui._widgets import Banner, ChatLog, CommandBar
from cairn.watcher import ReloadResult

if TYPE_CHECKING:
    from cairn.domain._sessions import Session
    from cairn.orchestrator import Orchestrator


def _app_with_reloader(session: Session, reloader: object) -> CairnApp:
    return CairnApp(
        orchestrator=cast("Orchestrator", Mock()),
        session=session,
        reloader=reloader,  # type: ignore[arg-type]
    )


class _SuccessReloader:
    def __init__(self) -> None:
        self.calls = 0

    async def reload(self) -> ReloadResult:
        self.calls += 1
        return ReloadResult(ok=True, summary="Reloaded: 1 config layer.")


class _FailingReloader:
    async def reload(self) -> ReloadResult:
        return ReloadResult(ok=False, summary="", error="schema_version missing")


class TestReloadCommand:
    @pytest.mark.asyncio
    async def test_reload_success_renders_banner(self, companion_session: Session) -> None:
        reloader = _SuccessReloader()
        app = _app_with_reloader(companion_session, reloader)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/reload"
            await bar.action_submit()
            await pilot.pause()

            banners = list(screen.query_one(ChatLog).query(Banner))
            rendered = "\n".join(str(b.renderable) for b in banners)
            assert "Reloaded: 1 config layer." in rendered
            assert reloader.calls == 1

    @pytest.mark.asyncio
    async def test_reload_failure_renders_warning(self, companion_session: Session) -> None:
        app = _app_with_reloader(companion_session, _FailingReloader())
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/reload"
            await bar.action_submit()
            await pilot.pause()

            banners = list(screen.query_one(ChatLog).query(Banner))
            rendered = "\n".join(str(b.renderable) for b in banners)
            assert "reload failed" in rendered
            assert "schema_version missing" in rendered
            warning = [b for b in banners if b.kind == "warning"]
            assert len(warning) == 1

    @pytest.mark.asyncio
    async def test_reload_without_reloader_shows_placeholder(
        self, companion_session: Session
    ) -> None:
        app = CairnApp(
            orchestrator=cast("Orchestrator", Mock()),
            session=companion_session,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            bar = screen.query_one(CommandBar)
            bar.value = "/reload"
            await bar.action_submit()
            await pilot.pause()

            banners = list(screen.query_one(ChatLog).query(Banner))
            assert any(
                "bootstrap did not wire a file watcher" in str(b.renderable) for b in banners
            )

    @pytest.mark.asyncio
    async def test_reload_mid_turn_queues(self, companion_session: Session) -> None:
        reloader = _SuccessReloader()
        app = _app_with_reloader(companion_session, reloader)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            # Pretend a turn is in flight.
            from cairn.ui._widgets import ActivityIndicator

            screen.query_one(ActivityIndicator).set_streaming()
            assert screen.is_turn_active() is True

            bar = screen.query_one(CommandBar)
            bar.value = "/reload"
            await bar.action_submit()
            await pilot.pause()

            # Reload was NOT executed yet.
            assert reloader.calls == 0
            banners = list(screen.query_one(ChatLog).query(Banner))
            rendered = "\n".join(str(b.renderable) for b in banners)
            assert "reload queued" in rendered

            # Simulate turn completion — pending reload flushes.
            screen.query_one(ActivityIndicator).set_idle()
            screen._flush_pending_reload()  # noqa: SLF001
            await pilot.pause()
            await pilot.pause()
            assert reloader.calls == 1
