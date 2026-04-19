"""UI events — emitted by the orchestrator, consumed by the UI layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UserMessagePersisted:
    """User message has been saved."""

    message_id: str


@dataclass(frozen=True, slots=True)
class AssistantTextDelta:
    """Incremental text from the assistant's response."""

    message_id: str
    text: str


@dataclass(frozen=True, slots=True)
class AssistantMessageComplete:
    """Assistant message is fully generated."""

    message_id: str


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    """A tool call has begun execution."""

    tool_call_id: str
    tool_name: str


@dataclass(frozen=True, slots=True)
class ToolCallCompleted:
    """A tool call has finished (success or failure)."""

    tool_call_id: str


@dataclass(frozen=True, slots=True)
class DelegationSpawned:
    """A delegation sub-session has been created."""

    session_id: str


@dataclass(frozen=True, slots=True)
class DelegationCompleted:
    """A delegation sub-session has finished."""

    session_id: str


@dataclass(frozen=True, slots=True)
class ObservationExtractionRequested:
    """Memory observation extraction has been queued."""

    session_id: str


@dataclass(frozen=True, slots=True)
class TurnComplete:
    """A full turn (user message → assistant response) has finished."""

    session_id: str


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
    | ToolCallStarted
    | ToolCallCompleted
    | DelegationSpawned
    | DelegationCompleted
    | ObservationExtractionRequested
    | TurnComplete
    | SessionCreated
    | SessionResumed
    | SessionArchived
)
