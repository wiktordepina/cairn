"""Persistence record shapes owned by the orchestrator brick."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — used by Pydantic at runtime

from pydantic import BaseModel, ConfigDict

from cairn.domain._enums import StopReason  # noqa: TCH001
from cairn.orchestrator._enums import TurnState  # noqa: TCH001


class TurnRecord(BaseModel):
    """A single row from the `turns` table."""

    model_config = ConfigDict(frozen=True)

    id: str
    session_id: str
    user_message_id: str
    state: TurnState
    iteration_count: int
    model: str
    started_at: datetime
    completed_at: datetime | None
    aborted_reason: str | None
    stop_reason: StopReason | None


class ApprovalDecisionRecord(BaseModel):
    """A single row from the `approval_decisions` table."""

    model_config = ConfigDict(frozen=True)

    id: int | None  # populated after insert
    tool_call_id: str
    decided_at: datetime
    decided_by: str
    decision: str  # 'approved' | 'rejected'
    reason: str | None
    args_snapshot_json: str | None
