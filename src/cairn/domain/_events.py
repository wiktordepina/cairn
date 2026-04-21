"""UI events — emitted by the orchestrator, consumed by the UI layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cairn.domain._enums import ErrorClass, StopReason, ToolCallStatus  # noqa: TCH001


@dataclass(frozen=True, slots=True)
class UserMessagePersisted:
    """User message has been saved."""

    message_id: str
    turn_id: str


@dataclass(frozen=True, slots=True)
class AssistantTextDelta:
    """Incremental text from the assistant's response."""

    message_id: str
    turn_id: str
    text: str


@dataclass(frozen=True, slots=True)
class AssistantMessageComplete:
    """Assistant message is fully generated."""

    message_id: str
    turn_id: str


@dataclass(frozen=True, slots=True)
class ToolCallPlanned:
    """Model has emitted a tool call; approval pending."""

    tool_call_id: str
    turn_id: str
    tool_name: str
    args: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCallApproved:
    """An approver cleared a tool call for execution."""

    tool_call_id: str
    turn_id: str
    approved_by: str


@dataclass(frozen=True, slots=True)
class ToolCallRejected:
    """A tool call was rejected before execution."""

    tool_call_id: str
    turn_id: str
    decided_by: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    """A tool call has begun executing."""

    tool_call_id: str
    turn_id: str
    tool_name: str


@dataclass(frozen=True, slots=True)
class ToolCallCompleted:
    """A tool call has finished. `status` carries the terminal state."""

    tool_call_id: str
    turn_id: str
    status: ToolCallStatus
    is_error: bool
    duration_ms: int | None


@dataclass(frozen=True, slots=True)
class DelegationSpawned:
    """A delegation sub-session has been created."""

    session_id: str
    turn_id: str


@dataclass(frozen=True, slots=True)
class DelegationCompleted:
    """A delegation sub-session has finished."""

    session_id: str
    turn_id: str


@dataclass(frozen=True, slots=True)
class ObservationExtractionRequested:
    """Memory observation extraction has been queued."""

    session_id: str
    turn_id: str


@dataclass(frozen=True, slots=True)
class TurnComplete:
    """A full turn (user message → assistant response) has finished cleanly."""

    session_id: str
    turn_id: str
    stop_reason: StopReason


@dataclass(frozen=True, slots=True)
class TurnAborted:
    """A turn was aborted before completion (cancel, crash, unrecoverable error)."""

    session_id: str
    turn_id: str
    reason: str  # 'user_cancel' | 'process_crash' | 'provider_failure' | 'turn_timeout' | 'error'
    error_class: ErrorClass | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class TurnBlocked:
    """A turn was blocked before running (e.g. budget cap exceeded)."""

    session_id: str
    turn_id: str
    reason: str
    message: str


@dataclass(frozen=True, slots=True)
class TurnIncomplete:
    """A turn stopped at `max_tokens` before a tool call finished streaming."""

    session_id: str
    turn_id: str


@dataclass(frozen=True, slots=True)
class BudgetWarning:
    """Session budget has crossed the soft-warning threshold."""

    session_id: str
    turn_id: str
    cost_usd: float
    threshold_usd: float


@dataclass(frozen=True, slots=True)
class SessionCreated:
    """A new session has been created."""

    session_id: str


@dataclass(frozen=True, slots=True)
class SessionResumed:
    """An existing session has been resumed."""

    session_id: str


@dataclass(frozen=True, slots=True)
class SessionArchived:
    """A session has been archived."""

    session_id: str


UIEvent = (
    UserMessagePersisted
    | AssistantTextDelta
    | AssistantMessageComplete
    | ToolCallPlanned
    | ToolCallApproved
    | ToolCallRejected
    | ToolCallStarted
    | ToolCallCompleted
    | DelegationSpawned
    | DelegationCompleted
    | ObservationExtractionRequested
    | TurnComplete
    | TurnAborted
    | TurnBlocked
    | TurnIncomplete
    | BudgetWarning
    | SessionCreated
    | SessionResumed
    | SessionArchived
)
