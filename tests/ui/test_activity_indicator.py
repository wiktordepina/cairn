"""Pilot + unit tests for the activity indicator + tool-row spinner."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from cairn.domain._enums import StopReason, ToolCallStatus
from cairn.domain._events import (
    AssistantTextDelta,
    ToolCallCompleted,
    ToolCallPlanned,
    ToolCallStarted,
    TurnAborted,
    TurnBlocked,
    TurnComplete,
)
from cairn.ui._app import CairnApp
from cairn.ui._observer import TextualUIEventObserver
from cairn.ui._widgets import ActivityIndicator, ToolRow

if TYPE_CHECKING:
    from cairn.domain._sessions import Session
    from cairn.orchestrator import Orchestrator


def _app_for(session: Session) -> CairnApp:
    return CairnApp(orchestrator=cast("Orchestrator", Mock()), session=session)


class TestActivityIndicatorUnit:
    def test_starts_idle(self) -> None:
        ind = ActivityIndicator()
        assert ind.state == "idle"
        assert ind.label == ""
        assert "-idle" in ind.classes

    def test_set_thinking_leaves_idle_class(self) -> None:
        ind = ActivityIndicator()
        ind.set_thinking()
        assert ind.state == "thinking"
        assert ind.label == "thinking"
        assert "-idle" not in ind.classes

    def test_set_tool_carries_name(self) -> None:
        ind = ActivityIndicator()
        ind.set_tool("web_fetch")
        assert ind.state == "tool"
        assert ind.label == "web_fetch"

    def test_set_idle_strips_label(self) -> None:
        ind = ActivityIndicator()
        ind.set_streaming()
        ind.set_idle()
        assert ind.state == "idle"
        assert ind.label == ""
        assert "-idle" in ind.classes


class TestActivityIndicatorWiring:
    @pytest.mark.asyncio
    async def test_dispatch_turn_enters_thinking(self, companion_session: Session) -> None:
        app = _app_for(companion_session)

        async def _empty_stream() -> None:
            if False:
                yield  # pragma: no cover

        app.orchestrator.run_turn = Mock(return_value=_empty_stream())

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            screen._dispatch_turn("hello")
            await pilot.pause()
            ind = screen.query_one(ActivityIndicator)
            assert ind.state == "thinking"

    @pytest.mark.asyncio
    async def test_first_delta_transitions_to_streaming(
        self, companion_session: Session
    ) -> None:
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            screen._activity.set_thinking()
            observer.observe(
                AssistantTextDelta(message_id="m-1", turn_id="t-1", text="Hi")
            )
            await pilot.pause()
            assert screen._activity.state == "streaming"

    @pytest.mark.asyncio
    async def test_tool_started_sets_tool_name(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            observer.observe(
                ToolCallPlanned(
                    tool_call_id="tc-1",
                    turn_id="t-1",
                    tool_name="web_fetch",
                    args={"url": "https://x"},
                )
            )
            observer.observe(
                ToolCallStarted(tool_call_id="tc-1", turn_id="t-1", tool_name="web_fetch")
            )
            await pilot.pause()
            ind = screen.query_one(ActivityIndicator)
            assert ind.state == "tool"
            assert ind.label == "web_fetch"

    @pytest.mark.asyncio
    async def test_tool_completed_returns_to_thinking(
        self, companion_session: Session
    ) -> None:
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            observer.observe(
                ToolCallPlanned(
                    tool_call_id="tc-1",
                    turn_id="t-1",
                    tool_name="file_read",
                    args={"path": "x"},
                )
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
                    duration_ms=12,
                )
            )
            await pilot.pause()
            assert screen._activity.state == "thinking"

    @pytest.mark.asyncio
    async def test_turn_complete_returns_to_idle(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            screen._activity.set_streaming()
            observer.observe(
                TurnComplete(
                    session_id="sess-1",
                    turn_id="t-1",
                    stop_reason=StopReason.END_TURN,
                )
            )
            await pilot.pause()
            assert screen._activity.state == "idle"

    @pytest.mark.asyncio
    async def test_turn_aborted_returns_to_idle(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            screen._activity.set_streaming()
            observer.observe(
                TurnAborted(
                    session_id="sess-1",
                    turn_id="t-1",
                    reason="error",
                    message="boom",
                )
            )
            await pilot.pause()
            assert screen._activity.state == "idle"

    @pytest.mark.asyncio
    async def test_turn_blocked_returns_to_idle(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            screen._activity.set_thinking()
            observer.observe(
                TurnBlocked(
                    session_id="sess-1",
                    turn_id="t-1",
                    reason="budget",
                    message="budget cap",
                )
            )
            await pilot.pause()
            assert screen._activity.state == "idle"


class TestToolRowSpinner:
    @pytest.mark.asyncio
    async def test_mark_running_starts_spinner(self, companion_session: Session) -> None:
        app = _app_for(companion_session)

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            row = ToolRow(tool_call_id="tc-1", tool_name="grep")
            screen._chat_log.append_tool_row(row)
            row.mark_running()
            await pilot.pause()
            assert row._spinner_timer is not None
            assert "running: grep" in str(row.renderable)

    @pytest.mark.asyncio
    async def test_mark_completed_stops_spinner(self, companion_session: Session) -> None:
        app = _app_for(companion_session)

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            row = ToolRow(tool_call_id="tc-1", tool_name="grep")
            screen._chat_log.append_tool_row(row)
            row.mark_running()
            row.mark_completed(
                status=ToolCallStatus.COMPLETED,
                is_error=False,
                duration_ms=42,
            )
            await pilot.pause()
            assert row._spinner_timer is None

    @pytest.mark.asyncio
    async def test_mark_rejected_stops_spinner(self, companion_session: Session) -> None:
        app = _app_for(companion_session)

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            row = ToolRow(tool_call_id="tc-1", tool_name="file_write")
            screen._chat_log.append_tool_row(row)
            row.mark_running()
            row.mark_rejected("user", "nope")
            await pilot.pause()
            assert row._spinner_timer is None


class TestCostPrecision:
    @pytest.mark.asyncio
    async def test_default_precision_is_six(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            screen.set_cost(0.000123)
            from cairn.ui._widgets import CostMeter

            meter = screen.query_one(CostMeter)
            assert meter.precision == 6
            assert "0.000123" in str(meter.renderable)

    @pytest.mark.asyncio
    async def test_override_precision(self, companion_session: Session) -> None:
        from cairn.config import UIConfig

        app = CairnApp(
            orchestrator=cast("Orchestrator", Mock()),
            session=companion_session,
            ui_config=UIConfig(cost_display_precision=2),
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            screen.set_cost(1.234567)
            from cairn.ui._widgets import CostMeter

            meter = screen.query_one(CostMeter)
            assert meter.precision == 2
            assert "1.23" in str(meter.renderable)
            assert "1.234" not in str(meter.renderable)
