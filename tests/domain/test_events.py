"""Tests for UI events."""

from __future__ import annotations

import dataclasses

import pytest

from cairn.domain._events import (
    AssistantMessageComplete,
    AssistantTextDelta,
    DelegationCompleted,
    DelegationSpawned,
    ObservationExtractionRequested,
    SessionArchived,
    SessionCreated,
    SessionResumed,
    ToolCallCompleted,
    ToolCallStarted,
    TurnComplete,
    UserMessagePersisted,
)


class TestUIEvents:
    def test_user_message_persisted(self) -> None:
        ev = UserMessagePersisted(message_id="msg-001")
        assert ev.message_id == "msg-001"

    def test_assistant_text_delta(self) -> None:
        ev = AssistantTextDelta(message_id="msg-002", text="hello")
        assert ev.text == "hello"

    def test_assistant_message_complete(self) -> None:
        ev = AssistantMessageComplete(message_id="msg-002")
        assert ev.message_id == "msg-002"

    def test_tool_call_started(self) -> None:
        ev = ToolCallStarted(tool_call_id="tc-1", tool_name="fetch")
        assert ev.tool_name == "fetch"

    def test_tool_call_completed(self) -> None:
        ev = ToolCallCompleted(tool_call_id="tc-1")
        assert ev.tool_call_id == "tc-1"

    def test_delegation_spawned(self) -> None:
        ev = DelegationSpawned(session_id="sub-001")
        assert ev.session_id == "sub-001"

    def test_delegation_completed(self) -> None:
        ev = DelegationCompleted(session_id="sub-001")
        assert ev.session_id == "sub-001"

    def test_observation_extraction_requested(self) -> None:
        ev = ObservationExtractionRequested(session_id="sess-001")
        assert ev.session_id == "sess-001"

    def test_turn_complete(self) -> None:
        ev = TurnComplete(session_id="sess-001")
        assert ev.session_id == "sess-001"

    def test_session_lifecycle_events(self) -> None:
        created = SessionCreated(session_id="sess-001")
        resumed = SessionResumed(session_id="sess-001")
        archived = SessionArchived(session_id="sess-001")
        assert created.session_id == "sess-001"
        assert resumed.session_id == "sess-001"
        assert archived.session_id == "sess-001"

    def test_all_frozen(self) -> None:
        # Test a representative subset — one with session_id, one with message_id
        with pytest.raises(dataclasses.FrozenInstanceError):
            SessionCreated(session_id="x").session_id = "y"  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            UserMessagePersisted(message_id="x").message_id = "y"  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            ToolCallStarted(tool_call_id="x", tool_name="x").tool_call_id = "y"  # type: ignore[misc]
