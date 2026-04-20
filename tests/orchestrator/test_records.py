"""Tests for TurnRecord and ApprovalDecisionRecord."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from cairn.domain._enums import StopReason
from cairn.orchestrator import ApprovalDecisionRecord, TurnRecord, TurnState


class TestTurnRecord:
    def test_construction(self) -> None:
        ts = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)
        rec = TurnRecord(
            id="t-1",
            session_id="sess-1",
            user_message_id="msg-1",
            state=TurnState.COMPLETED,
            iteration_count=1,
            model="claude-opus-4-7",
            started_at=ts,
            completed_at=ts,
            aborted_reason=None,
            stop_reason=StopReason.END_TURN,
        )
        assert rec.state is TurnState.COMPLETED
        assert rec.stop_reason is StopReason.END_TURN

    def test_frozen(self) -> None:
        ts = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)
        rec = TurnRecord(
            id="t-1",
            session_id="sess-1",
            user_message_id="msg-1",
            state=TurnState.STARTED,
            iteration_count=0,
            model="x",
            started_at=ts,
            completed_at=None,
            aborted_reason=None,
            stop_reason=None,
        )
        with pytest.raises(ValidationError):
            rec.state = TurnState.COMPLETED  # type: ignore[misc]


class TestApprovalDecisionRecord:
    def test_construction(self) -> None:
        ts = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)
        rec = ApprovalDecisionRecord(
            id=1,
            tool_call_id="tc-1",
            decided_at=ts,
            decided_by="user",
            decision="approved",
            reason=None,
            args_snapshot_json=None,
        )
        assert rec.decision == "approved"
