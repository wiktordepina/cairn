"""Pilot + unit tests for the ThinkingRow widget and its observer wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from cairn.domain import (
    AssistantMessageComplete,
    AssistantTextDelta,
    AssistantThinkingDelta,
    ToolCallPlanned,
)
from cairn.ui._app import CairnApp
from cairn.ui._observer import TextualUIEventObserver
from cairn.ui._widgets import ChatLog, MessageView, ThinkingRow, ToolRow

if TYPE_CHECKING:
    from cairn.domain._sessions import Session
    from cairn.orchestrator import Orchestrator


def _app_for(session: Session) -> CairnApp:
    return CairnApp(orchestrator=cast("Orchestrator", Mock()), session=session)


class TestThinkingRowWidget:
    @pytest.mark.asyncio
    async def test_starts_collapsed(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        row = ThinkingRow()
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            screen.query_one(ChatLog).append_thinking_row(row)
            await pilot.pause()
            assert row.expanded is False
            assert "-expanded" not in row.classes

    @pytest.mark.asyncio
    async def test_action_toggle_expands_then_collapses(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        row = ThinkingRow()
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            screen.query_one(ChatLog).append_thinking_row(row)
            await pilot.pause()
            row.action_toggle_expanded()
            await pilot.pause()
            assert row.expanded is True
            assert "-expanded" in row.classes
            row.action_toggle_expanded()
            await pilot.pause()
            assert row.expanded is False
            assert "-expanded" not in row.classes

    @pytest.mark.asyncio
    async def test_append_delta_buffers_text(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        row = ThinkingRow()
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            screen.query_one(ChatLog).append_thinking_row(row)
            row.append_delta("Hmm,")
            row.append_delta(" let me think.")
            await pilot.pause()
            assert row.text == "Hmm, let me think."

    @pytest.mark.asyncio
    async def test_seal_freezes_header_and_is_idempotent(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        row = ThinkingRow()
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            screen.query_one(ChatLog).append_thinking_row(row)
            row.append_delta("done")
            await pilot.pause()
            row.seal()
            assert row.sealed is True
            elapsed_first = row._sealed_elapsed_s
            await pilot.pause()
            row.seal()
            assert row._sealed_elapsed_s == elapsed_first


class TestObserverRoutesThinkingDelta:
    @pytest.mark.asyncio
    async def test_thinking_delta_mounts_a_thinking_row(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            observer.observe(
                AssistantThinkingDelta(message_id="msg-1", turn_id="t-1", text="thinking…")
            )
            await pilot.pause()

            screen = app.current_session_screen
            assert screen is not None
            chat_log = screen.query_one(ChatLog)
            rows = list(chat_log.query(ThinkingRow))
            assert len(rows) == 1
            assert rows[0].text == "thinking…"

    @pytest.mark.asyncio
    async def test_consecutive_deltas_share_one_row(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            for chunk in ("part 1 ", "part 2 ", "part 3"):
                observer.observe(
                    AssistantThinkingDelta(message_id="msg-1", turn_id="t-1", text=chunk)
                )
            await pilot.pause()

            screen = app.current_session_screen
            assert screen is not None
            rows = list(screen.query_one(ChatLog).query(ThinkingRow))
            assert len(rows) == 1
            assert rows[0].text == "part 1 part 2 part 3"

    @pytest.mark.asyncio
    async def test_text_delta_seals_pending_thinking_row(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            observer.observe(
                AssistantThinkingDelta(message_id="msg-1", turn_id="t-1", text="warming up")
            )
            await pilot.pause()
            observer.observe(
                AssistantTextDelta(message_id="msg-1", turn_id="t-1", text="Here we go.")
            )
            await pilot.pause()

            screen = app.current_session_screen
            assert screen is not None
            chat_log = screen.query_one(ChatLog)
            rows = list(chat_log.query(ThinkingRow))
            messages = list(chat_log.query(MessageView))
            assert len(rows) == 1
            assert rows[0].sealed is True
            assert len(messages) == 1
            # And a fresh thinking phase after the text gets a *new* row.
            observer.observe(
                AssistantThinkingDelta(message_id="msg-1", turn_id="t-1", text="more thinking")
            )
            await pilot.pause()
            rows = list(chat_log.query(ThinkingRow))
            assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_tool_plan_seals_pending_thinking_row(self, companion_session: Session) -> None:
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            observer.observe(
                AssistantThinkingDelta(message_id="msg-1", turn_id="t-1", text="planning")
            )
            await pilot.pause()
            observer.observe(
                ToolCallPlanned(
                    tool_call_id="tc-1",
                    turn_id="t-1",
                    tool_name="file_read",
                    args={"path": "x"},
                )
            )
            await pilot.pause()

            screen = app.current_session_screen
            assert screen is not None
            chat_log = screen.query_one(ChatLog)
            rows = list(chat_log.query(ThinkingRow))
            tool_rows = list(chat_log.query(ToolRow))
            assert len(rows) == 1
            assert rows[0].sealed is True
            assert len(tool_rows) == 1

    @pytest.mark.asyncio
    async def test_assistant_message_complete_seals_pending_thinking(
        self, companion_session: Session
    ) -> None:
        # Thinking-only response (no text, no tool calls) should still
        # get its row sealed when the message completes.
        app = _app_for(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            observer.observe(
                AssistantThinkingDelta(message_id="msg-1", turn_id="t-1", text="silent thought")
            )
            await pilot.pause()
            observer.observe(AssistantMessageComplete(message_id="msg-1", turn_id="t-1"))
            await pilot.pause()

            screen = app.current_session_screen
            assert screen is not None
            rows = list(screen.query_one(ChatLog).query(ThinkingRow))
            assert len(rows) == 1
            assert rows[0].sealed is True
