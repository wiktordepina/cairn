"""Domain enums — shared across the entire cairn codebase."""

from __future__ import annotations

from enum import StrEnum


class SessionType(StrEnum):
    """Type of session within cairn."""

    COMPANION = "companion"
    PERSONA = "persona"
    EPHEMERAL = "ephemeral"


class StopReason(StrEnum):
    """Why a model stopped generating."""

    END_TURN = "end_turn"
    MAX_TOKENS = "max_tokens"
    TOOL_USE = "tool_use"


class UsageOperation(StrEnum):
    """What triggered a provider call — for cost attribution."""

    PRIMARY_TURN = "primary_turn"
    DELEGATION = "delegation"
    COMPACTION = "compaction"
    EXTRACTION = "extraction"
    REFLECTION = "reflection"
    TITLING = "titling"
    EMBEDDING = "embedding"


class ToolCallStatus(StrEnum):
    """Lifecycle state of a tool call."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ErrorClass(StrEnum):
    """Classification of errors for retry and reporting."""

    TRANSIENT = "transient"
    LLM = "llm"
    USER = "user"
    UNEXPECTED = "unexpected"


class MemoryEntryType(StrEnum):
    """Category of a memory observation."""

    FACT = "fact"
    PREFERENCE = "preference"
    INSIGHT = "insight"
    RELATIONSHIP = "relationship"
    EVENT = "event"
    TASK = "task"


class MemoryClass(StrEnum):
    """Temporal class of a memory entry — affects decay rate."""

    SEMANTIC = "semantic"
    EPISODIC = "episodic"
