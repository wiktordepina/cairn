"""Tests for UI events."""

from __future__ import annotations

import dataclasses

import pytest

from cairn.domain._enums import ErrorClass, StopReason, ToolCallStatus
from cairn.domain._events import (
    AssistantMessageComplete,
    AssistantTextDelta,
    BudgetWarning,
    DelegationCompleted,
    DelegationSpawned,
    ObservationExtractionCompleted,
    ObservationExtractionRequested,
    SessionArchived,
    SessionCreated,
    SessionResumed,
    ToolCallApproved,
    ToolCallCompleted,
    ToolCallPlanned,
    ToolCallRejected,
    ToolCallStarted,
    TurnAborted,
    TurnBlocked,
    TurnComplete,
    TurnIncomplete,
    UserMessagePersisted,
)


class TestUIEvents:
    def test_user_message_persisted(self) -> None:
        ev = UserMessagePersisted(message_id="msg-001", turn_id="t-1")
        assert ev.message_id == "msg-001"
        assert ev.turn_id == "t-1"

    def test_assistant_text_delta(self) -> None:
        ev = AssistantTextDelta(message_id="msg-002", turn_id="t-1", text="hello")
        assert ev.text == "hello"
        assert ev.turn_id == "t-1"

    def test_assistant_message_complete(self) -> None:
        ev = AssistantMessageComplete(message_id="msg-002", turn_id="t-1")
        assert ev.message_id == "msg-002"

    def test_tool_call_planned(self) -> None:
        ev = ToolCallPlanned(
            tool_call_id="tc-1", turn_id="t-1", tool_name="fetch", args={"url": "x"}
        )
        assert ev.tool_name == "fetch"
        assert ev.args == {"url": "x"}

    def test_tool_call_approved(self) -> None:
        ev = ToolCallApproved(tool_call_id="tc-1", turn_id="t-1", approved_by="user")
        assert ev.approved_by == "user"

    def test_tool_call_rejected(self) -> None:
        ev = ToolCallRejected(tool_call_id="tc-1", turn_id="t-1", decided_by="user", reason="no")
        assert ev.reason == "no"

    def test_tool_call_started(self) -> None:
        ev = ToolCallStarted(tool_call_id="tc-1", turn_id="t-1", tool_name="fetch")
        assert ev.tool_name == "fetch"

    def test_tool_call_completed(self) -> None:
        ev = ToolCallCompleted(
            tool_call_id="tc-1",
            turn_id="t-1",
            status=ToolCallStatus.COMPLETED,
            is_error=False,
            duration_ms=42,
        )
        assert ev.status is ToolCallStatus.COMPLETED
        assert ev.duration_ms == 42

    def test_delegation_spawned(self) -> None:
        ev = DelegationSpawned(session_id="sub-001", turn_id="t-1")
        assert ev.session_id == "sub-001"

    def test_delegation_completed(self) -> None:
        ev = DelegationCompleted(session_id="sub-001", turn_id="t-1")
        assert ev.session_id == "sub-001"

    def test_observation_extraction_requested(self) -> None:
        ev = ObservationExtractionRequested(session_id="sess-001", turn_id="t-1")
        assert ev.session_id == "sess-001"

    def test_observation_extraction_completed_defaults(self) -> None:
        ev = ObservationExtractionCompleted(
            session_id="sess-001",
            turn_id="t-1",
            status="succeeded",
        )
        assert ev.observations_written == 0
        assert ev.cost_usd == 0.0
        assert ev.reason is None

    def test_observation_extraction_completed_with_detail(self) -> None:
        ev = ObservationExtractionCompleted(
            session_id="sess-001",
            turn_id="t-1",
            status="gated",
            reason="persona_opt_out",
        )
        assert ev.status == "gated"
        assert ev.reason == "persona_opt_out"

    def test_turn_complete(self) -> None:
        ev = TurnComplete(session_id="sess-001", turn_id="t-1", stop_reason=StopReason.END_TURN)
        assert ev.session_id == "sess-001"
        assert ev.stop_reason is StopReason.END_TURN

    def test_turn_aborted(self) -> None:
        ev = TurnAborted(
            session_id="sess-001",
            turn_id="t-1",
            reason="user_cancel",
            error_class=None,
            message=None,
        )
        assert ev.reason == "user_cancel"

    def test_turn_aborted_with_error(self) -> None:
        ev = TurnAborted(
            session_id="sess-001",
            turn_id="t-1",
            reason="provider_failure",
            error_class=ErrorClass.TRANSIENT,
            message="network down",
        )
        assert ev.error_class is ErrorClass.TRANSIENT
        assert ev.message == "network down"

    def test_turn_blocked(self) -> None:
        ev = TurnBlocked(
            session_id="sess-001",
            turn_id="t-1",
            reason="budget",
            message="session cap exceeded",
        )
        assert ev.reason == "budget"

    def test_turn_incomplete(self) -> None:
        ev = TurnIncomplete(session_id="sess-001", turn_id="t-1")
        assert ev.session_id == "sess-001"

    def test_budget_warning(self) -> None:
        ev = BudgetWarning(session_id="sess-001", turn_id="t-1", cost_usd=4.2, threshold_usd=4.0)
        assert ev.cost_usd == 4.2
        assert ev.threshold_usd == 4.0

    def test_session_lifecycle_events(self) -> None:
        created = SessionCreated(session_id="sess-001")
        resumed = SessionResumed(session_id="sess-001")
        archived = SessionArchived(session_id="sess-001")
        assert created.session_id == "sess-001"
        assert resumed.session_id == "sess-001"
        assert archived.session_id == "sess-001"

    def test_all_frozen(self) -> None:
        # Representative spot-check of frozen-ness.
        with pytest.raises(dataclasses.FrozenInstanceError):
            SessionCreated(session_id="x").session_id = "y"  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            UserMessagePersisted(message_id="x", turn_id="t").message_id = "y"  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            ToolCallStarted(tool_call_id="x", turn_id="t", tool_name="x").tool_call_id = "y"  # type: ignore[misc]
