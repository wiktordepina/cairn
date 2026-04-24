"""Pilot integration tests for `SessionScreen`.

Drives `CairnApp.run_test()` with fake collaborators so widget
lifecycle (mount, compose, observer routing into real widgets) is
exercised end-to-end without real providers or persistence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from cairn.domain import (
    AssistantMessageComplete,
    AssistantTextDelta,
    StopReason,
    TurnComplete,
    UserMessagePersisted,
)
from cairn.ui._app import CairnApp
from cairn.ui._observer import TextualUIEventObserver
from cairn.ui._widgets import ChatLog, MessageView

if TYPE_CHECKING:
    from cairn.domain import Session
    from cairn.orchestrator import Orchestrator


def _mint_app(session: Session) -> CairnApp:
    """Mint a CairnApp with a Mock orchestrator — enough for screen tests."""
    orchestrator = cast("Orchestrator", Mock())
    return CairnApp(orchestrator=orchestrator, session=session)


class TestStreamingFlow:
    @pytest.mark.asyncio
    async def test_assistant_stream_accumulates_in_messageview(
        self, companion_session: Session
    ) -> None:
        app = _mint_app(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()

            # Stage + fire user message.
            screen = app.current_session_screen
            assert screen is not None
            screen.stage_user_message(message_id="msg-u1", text="hello")
            observer.observe(UserMessagePersisted(message_id="msg-u1", turn_id="t-1"))

            # Stream three deltas.
            for chunk in ("Hi ", "there", "!"):
                observer.observe(
                    AssistantTextDelta(message_id="msg-a1", turn_id="t-1", text=chunk)
                )

            observer.observe(AssistantMessageComplete(message_id="msg-a1", turn_id="t-1"))
            observer.observe(
                TurnComplete(
                    session_id=companion_session.id,
                    turn_id="t-1",
                    stop_reason=StopReason.END_TURN,
                )
            )
            await pilot.pause()

            chat_log = screen.query_one(ChatLog)
            user_view = chat_log.find_message("msg-u1")
            assistant_view = chat_log.find_message("msg-a1")

            assert user_view is not None
            assert user_view.role == "user"
            assert user_view.text == "hello"

            assert assistant_view is not None
            assert assistant_view.role == "assistant"
            assert assistant_view.text == "Hi there!"
            assert "-sealed" in assistant_view.classes

    @pytest.mark.asyncio
    async def test_assistant_without_prior_user_message_still_renders(
        self, companion_session: Session
    ) -> None:
        """An assistant delta for an unseen message id auto-creates the view."""
        app = _mint_app(companion_session)
        observer = TextualUIEventObserver(app)

        async with app.run_test() as pilot:
            await pilot.pause()
            observer.observe(AssistantTextDelta(message_id="msg-a1", turn_id="t-1", text="hello"))
            await pilot.pause()

            screen = app.current_session_screen
            assert screen is not None
            chat_log = screen.query_one(ChatLog)
            view = chat_log.find_message("msg-a1")
            assert view is not None
            assert isinstance(view, MessageView)
            assert view.text == "hello"
            # Not sealed until AssistantMessageComplete arrives.
            assert "-sealed" not in view.classes


class TestChatLogLookup:
    @pytest.mark.asyncio
    async def test_find_message_returns_none_for_unknown_id(
        self, companion_session: Session
    ) -> None:
        app = _mint_app(companion_session)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.current_session_screen
            assert screen is not None
            chat_log = screen.query_one(ChatLog)
            assert chat_log.find_message("nope") is None
