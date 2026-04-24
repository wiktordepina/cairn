"""Pilot tests for the crash-recovery banner."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from cairn.ui._app import CairnApp
from cairn.ui._widgets import Banner, ChatLog

if TYPE_CHECKING:
    from cairn.domain._sessions import Session
    from cairn.orchestrator import Orchestrator


def _app_for(session: Session, *, resumed: int = 0) -> CairnApp:
    return CairnApp(
        orchestrator=cast("Orchestrator", Mock()),
        session=session,
        resumed_turn_count=resumed,
    )


class TestCrashRecoveryBanner:
    @pytest.mark.asyncio
    async def test_no_banner_when_count_zero(self, companion_session: Session) -> None:
        app = _app_for(companion_session, resumed=0)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            chat_log = screen.query_one(ChatLog)
            banners = list(chat_log.query(Banner))
            assert banners == []

    @pytest.mark.asyncio
    async def test_banner_shown_for_single_turn(self, companion_session: Session) -> None:
        app = _app_for(companion_session, resumed=1)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            chat_log = screen.query_one(ChatLog)
            banners = list(chat_log.query(Banner))
            assert len(banners) == 1
            rendered = str(banners[0].renderable)
            assert "recovered 1 aborted turn" in rendered
            assert "from previous run" in rendered
            assert banners[0].kind == "muted"

    @pytest.mark.asyncio
    async def test_banner_shown_for_multiple_turns(self, companion_session: Session) -> None:
        app = _app_for(companion_session, resumed=3)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            rendered = str(screen.query_one(Banner).renderable)
            assert "recovered 3 aborted turns" in rendered

    @pytest.mark.asyncio
    async def test_count_cleared_after_first_read(self, companion_session: Session) -> None:
        """`take_resumed_turn_count` clears the counter so a hypothetical
        second screen mount does not duplicate the banner."""
        app = _app_for(companion_session, resumed=2)
        async with app.run_test() as pilot:
            await pilot.pause()
            # First read: non-zero.
            first_count = app.take_resumed_turn_count()
            # Second call is after SessionScreen already consumed it
            # on mount, so this assertion checks idempotence.
            second_count = app.take_resumed_turn_count()
            assert first_count == 0  # already consumed by on_mount
            assert second_count == 0
