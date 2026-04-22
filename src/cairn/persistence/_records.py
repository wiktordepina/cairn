"""Frozen Pydantic records returned from the persistence layer."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Literal

from pydantic import BaseModel, ConfigDict

from cairn.domain._enums import (  # noqa: TC001
    ErrorClass,
    StopReason,
    ToolCallStatus,
    UsageOperation,
)
from cairn.domain._memory import MemoryEntry  # noqa: TC001


class ToolCallRecord(BaseModel):
    """A single tool call as stored in the `tool_calls` table."""

    model_config = ConfigDict(frozen=True)

    id: str
    session_id: str
    message_id: str
    turn_id: str | None = None
    tool_name: str
    tool_kind: Literal["native", "mcp", "delegation"]
    server: str | None = None
    input_json: str
    input_truncated: bool = False
    output_json: str | None = None
    output_truncated: bool = False
    output_bytes: int | None = None
    status: ToolCallStatus
    is_error: bool = False
    error_class: ErrorClass | None = None
    error_message: str | None = None
    is_delegation: bool = False
    delegation_session_id: str | None = None
    approval_required: bool = False
    approved_by: Literal["user", "auto"] | None = None
    approved_at: datetime | None = None
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None


class UsageRecord(BaseModel):
    """A single provider call recorded in `model_usage`."""

    model_config = ConfigDict(frozen=True)

    id: int
    timestamp: datetime
    session_id: str | None = None
    message_id: str | None = None
    turn_id: str | None = None
    parent_session_id: str | None = None
    provider: str
    model: str
    role: str
    operation: UsageOperation
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int | None = None
    stop_reason: StopReason | None = None
    is_error: bool = False
    metadata: dict[str, object] = {}


class MemoryHit(BaseModel):
    """A memory entry surfaced by `MemoryRepo.search`, paired with its
    FTS5 BM25 score.

    Lower `bm25_score` is a better match per SQLite's FTS5 convention.
    Callers typically rescore with recency + importance weights before
    picking top-k — see the retrieval service.
    """

    model_config = ConfigDict(frozen=True)

    entry: MemoryEntry
    bm25_score: float
