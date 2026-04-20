"""Provider types — request/response shapes and streaming events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from cairn.domain._enums import StopReason  # noqa: TCH001

if TYPE_CHECKING:
    from cairn.domain._messages import Message


class ToolDefinition(BaseModel):
    """Schema for a tool exposed to the model."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """A request to send to an LLM provider."""

    model: str
    messages: list[Message]
    system: str | None = None
    tools: list[ToolDefinition] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    max_tokens: int = 4096
    temperature: float | None = None
    stop_sequences: list[str] | None = None


# ---------------------------------------------------------------------------
# Provider events — streamed from Provider.stream()
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextDelta:
    """Incremental text token from the model."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolCallStart:
    """A tool call has begun (ID and name known)."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    """Partial JSON input for a tool call."""

    id: str
    input_delta: str


@dataclass(frozen=True, slots=True)
class ToolCallEnd:
    """A tool call's input is fully received."""

    id: str


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """Token consumption from a provider call."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True, slots=True)
class MessageStop:
    """The model has finished generating."""

    stop_reason: StopReason


ProviderEvent = TextDelta | ToolCallStart | ToolCallDelta | ToolCallEnd | UsageEvent | MessageStop
