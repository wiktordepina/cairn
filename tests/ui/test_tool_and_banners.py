"""Pilot + unit tests for tool-row lifecycle + turn-state banners."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from cairn.domain import (
    BudgetOverflowAdvisory,
    HistoryCompacted,
    ToolCallApproved,
    ToolCallCompleted,
    ToolCallPlanned,
    ToolCallRejected,
    ToolCallStarted,
    ToolCallStatus,
    TurnAborted,
    TurnBlocked,
    TurnIncomplete,
)
from cairn.ui._app import CairnApp
from cairn.ui._observer import TextualUIEventObserver
from cairn.ui._widgets import Banner, ChatLog

if TYPE_CHECKING:
    from cairn.domain._sessions import Session
    from cairn.orchestrator import Orchestrator


def _app_for(session: Session) -> CairnApp:
    return CairnApp(orchestrator=cast("Orchestrator", Mock()), session=session)


class TestToolRowLifecycle:
    @pytest.mark.asyncio
    async def test_planned_then_approved_then_running_then_completed(
        self, companion_session: Session
    ) -> None:
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            observer.observe(
                ToolCallPlanned(
                    tool_call_id="tc-1", turn_id="t-1", tool_name="file_read", args={"path": "x"}
                )
            )
            observer.observe(
                ToolCallApproved(tool_call_id="tc-1", turn_id="t-1", approved_by="auto:read-only")
            )
            observer.observe(
                ToolCallStarted(tool_call_id="tc-1", turn_id="t-1", tool_name="file_read")
            )
            observer.observe(
                ToolCallCompleted(
                    tool_call_id="tc-1",
                    turn_id="t-1",
                    status=ToolCallStatus.COMPLETED,
                    is_error=False,
                    duration_ms=42,
                )
            )
            await pilot.pause()

            screen = app.current_session_screen
            assert screen is not None
            chat_log = screen.query_one(ChatLog)
            row = chat_log.find_tool_row("tc-1")
            assert row is not None
            assert row.status is ToolCallStatus.COMPLETED
            assert "-completed" in row.classes
            assert "-error" not in row.classes
            rendered = str(row.renderable)
            assert "completed" in rendered
            assert "42 ms" in rendered

    @pytest.mark.asyncio
    async def test_rejected_tool_marks_error(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            observer.observe(
                ToolCallPlanned(
                    tool_call_id="tc-1",
                    turn_id="t-1",
                    tool_name="file_write",
                    args={"path": "x"},
                )
            )
            observer.observe(
                ToolCallRejected(
                    tool_call_id="tc-1",
                    turn_id="t-1",
                    decided_by="user",
                    reason="dangerous",
                )
            )
            await pilot.pause()

            screen = app.current_session_screen
            assert screen is not None
            chat_log = screen.query_one(ChatLog)
            row = chat_log.find_tool_row("tc-1")
            assert row is not None
            assert "-error" in row.classes
            assert "dangerous" in str(row.renderable)

    @pytest.mark.asyncio
    async def test_failed_tool_marks_error_with_status(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            observer.observe(
                ToolCallPlanned(
                    tool_call_id="tc-1", turn_id="t-1", tool_name="grep", args={"pattern": "x"}
                )
            )
            observer.observe(
                ToolCallCompleted(
                    tool_call_id="tc-1",
                    turn_id="t-1",
                    status=ToolCallStatus.FAILED,
                    is_error=True,
                    duration_ms=None,
                )
            )
            await pilot.pause()

            screen = app.current_session_screen
            assert screen is not None
            chat_log = screen.query_one(ChatLog)
            row = chat_log.find_tool_row("tc-1")
            assert row is not None
            assert "-error" in row.classes
            assert "failed" in str(row.renderable)


class TestTurnBanners:
    @pytest.mark.asyncio
    async def test_turn_aborted_renders_error_banner(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            observer.observe(
                TurnAborted(
                    session_id=companion_session.id,
                    turn_id="t-1",
                    reason="provider_failure",
                    message="API down",
                )
            )
            await pilot.pause()

            screen = app.current_session_screen
            assert screen is not None
            chat_log = screen.query_one(ChatLog)
            banners = list(chat_log.query(Banner))
            assert len(banners) == 1
            assert banners[0].kind == "error"
            assert "API down" in str(banners[0].renderable)

    @pytest.mark.asyncio
    async def test_turn_aborted_with_timeout_reason_renders_warning_banner(
        self, companion_session: Session
    ) -> None:
        # turn_timeout is a soft-cancel: tools in flight finished, model
        # just didn't get to wrap up. Banner is warning-tinted (not
        # error) and explains that tool side-effects are intact.
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            observer.observe(
                TurnAborted(
                    session_id=companion_session.id,
                    turn_id="t-1",
                    reason="turn_timeout",
                )
            )
            await pilot.pause()

            screen = app.current_session_screen
            assert screen is not None
            chat_log = screen.query_one(ChatLog)
            banners = list(chat_log.query(Banner))
            assert len(banners) == 1
            assert banners[0].kind == "warning"
            text = str(banners[0].renderable)
            assert "deadline" in text
            assert "Tools in flight finished normally" in text

    @pytest.mark.asyncio
    async def test_turn_blocked_renders_warning_banner(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            observer.observe(
                TurnBlocked(
                    session_id=companion_session.id,
                    turn_id="t-1",
                    reason="budget",
                    message="session cap reached",
                )
            )
            await pilot.pause()

            screen = app.current_session_screen
            assert screen is not None
            chat_log = screen.query_one(ChatLog)
            banners = list(chat_log.query(Banner))
            assert len(banners) == 1
            assert banners[0].kind == "warning"
            assert "session cap reached" in str(banners[0].renderable)

    @pytest.mark.asyncio
    async def test_turn_incomplete_renders_muted_banner(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            observer.observe(TurnIncomplete(session_id=companion_session.id, turn_id="t-1"))
            await pilot.pause()

            screen = app.current_session_screen
            assert screen is not None
            chat_log = screen.query_one(ChatLog)
            banners = list(chat_log.query(Banner))
            assert len(banners) == 1
            assert banners[0].kind == "muted"

    @pytest.mark.asyncio
    async def test_history_compacted_banner_shows_token_delta(
        self, companion_session: Session
    ) -> None:
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            observer.observe(
                HistoryCompacted(
                    session_id=companion_session.id,
                    turn_id="t-1",
                    blocks_dropped=3,
                    messages_dropped=6,
                    tokens_before=100_000,
                    tokens_after=40_000,
                    reason="budget",
                )
            )
            await pilot.pause()

            screen = app.current_session_screen
            assert screen is not None
            chat_log = screen.query_one(ChatLog)
            banners = list(chat_log.query(Banner))
            assert len(banners) == 1
            rendered = str(banners[0].renderable)
            assert "100000" in rendered.replace(",", "")
            assert "40000" in rendered.replace(",", "")
            assert "6 messages" in rendered

    @pytest.mark.asyncio
    async def test_overflow_advisory_banner_shows_projected_tokens(
        self, companion_session: Session
    ) -> None:
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            observer.observe(
                BudgetOverflowAdvisory(
                    session_id=companion_session.id,
                    turn_id="t-1",
                    tokens_projected=210_000,
                    context_window=200_000,
                    safety_margin=5_000,
                    overflow_tokens=10_000,
                    will_fit_context_window=False,
                )
            )
            await pilot.pause()

            screen = app.current_session_screen
            assert screen is not None
            chat_log = screen.query_one(ChatLog)
            banners = list(chat_log.query(Banner))
            assert len(banners) == 1
            assert banners[0].kind == "warning"
            rendered = str(banners[0].renderable).replace(",", "")
            assert "210000" in rendered
            assert "10000" in rendered
