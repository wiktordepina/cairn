"""UI events — emitted by the orchestrator, consumed by the UI layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TCH003
from pathlib import Path  # noqa: TCH003
from typing import Any, Literal

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
class AssistantThinkingDelta:
    """Incremental reasoning text from the assistant.

    Surfaced separately from `AssistantTextDelta` so the UI can
    render thinking in a distinct (collapsible, muted) widget.
    The leading `ThinkingBlock` on the assistant message holds
    the full accumulated reasoning; this event is the streaming
    counterpart of `provider.ThinkingDelta`.
    """

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
class ObservationExtractionCompleted:
    """Memory observation extraction has finished.

    Pairs 1:1 with an earlier `ObservationExtractionRequested` for the
    same `turn_id`. Fires on every post-submit terminal path — gates,
    successes, and failures — so the UI can always close the loop on
    the earlier request indicator.

    `status` is one of:

    - ``"succeeded"`` — extractor ran and wrote ≥ 0 observations cleanly.
    - ``"gated"`` — queue short-circuited before calling the extractor
      (persona opt-out, length gate, memoryless session, missing
      session, no latest messages). `reason` carries the specific gate.
    - ``"failed"`` — extractor raised, parsing failed, or the cost cap
      fired before any observation could be written. `reason` carries
      detail where available.
    """

    session_id: str
    turn_id: str
    status: str  # 'succeeded' | 'gated' | 'failed'
    observations_written: int = 0
    cost_usd: float = 0.0
    reason: str | None = None


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
class HistoryCompacted:
    """Conversation history was truncated before the provider call."""

    session_id: str
    turn_id: str
    blocks_dropped: int
    messages_dropped: int
    tokens_before: int
    tokens_after: int
    reason: str  # 'budget' | 'preserve_floor_hit'


@dataclass(frozen=True, slots=True)
class BudgetOverflowAdvisory:
    """Compaction hit the preserve-floor and the request still exceeds the
    advisory budget. Awaits a user decision via `BudgetOverflowGateway`."""

    session_id: str
    turn_id: str
    tokens_projected: int
    context_window: int
    safety_margin: int
    overflow_tokens: int
    will_fit_context_window: bool


@dataclass(frozen=True, slots=True)
class DriftChange:
    """One file's drift state observed by the file watcher."""

    category: Literal["config", "convention", "profile_doc"]
    path: Path
    kind: Literal["modified", "removed"]


@dataclass(frozen=True, slots=True)
class ConfigDriftDetected:
    """Watched files have changed on disk since the last snapshot.

    Process-level signal — not turn-scoped. Emitted on the rising edge
    of drift only; the watcher buffers the event during an active turn
    so the banner doesn't appear above a streaming assistant message.
    """

    detected_at: datetime
    changes: tuple[DriftChange, ...]


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
    | AssistantThinkingDelta
    | AssistantMessageComplete
    | ToolCallPlanned
    | ToolCallApproved
    | ToolCallRejected
    | ToolCallStarted
    | ToolCallCompleted
    | DelegationSpawned
    | DelegationCompleted
    | ObservationExtractionRequested
    | ObservationExtractionCompleted
    | TurnComplete
    | TurnAborted
    | TurnBlocked
    | TurnIncomplete
    | BudgetWarning
    | HistoryCompacted
    | BudgetOverflowAdvisory
    | SessionCreated
    | SessionResumed
    | SessionArchived
    | ConfigDriftDetected
)
