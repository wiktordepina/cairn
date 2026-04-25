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
class SystemPromptSegment:
    """One region of the system prompt with a stability hint.

    Provider adapters use ``cacheable`` to decide where to drop cache
    breakpoints; callers don't touch provider-specific cache markers
    directly. A breakpoint is placed at the END of every segment whose
    ``cacheable`` is True.

    Attributes:
        text: The segment's text content.
        cacheable: When True, the end of this segment is a cache
            breakpoint (provider-side translation may emit
            ``cache_control`` or equivalent). Defaults to False.
    """

    text: str
    cacheable: bool = False


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """A request to send to an LLM provider.

    Attributes:
        model: Model identifier (provider-scoped).
        messages: Conversation history (oldest → newest).
        system: System prompt. ``str`` for the simple flat path;
            ``list[SystemPromptSegment]`` opts into prompt-cache-aware
            translation where each cacheable segment terminates at a
            cache breakpoint.
        tools: Tool catalogue.
        max_tokens: Output cap.
        temperature: Sampling temperature; ``None`` uses provider default.
        stop_sequences: Optional stop sequences.
        cache_tools: When True AND ``tools`` non-empty, providers that
            support explicit cache markers (Anthropic, OpenRouter on an
            Anthropic backend) emit a breakpoint at the end of the tool
            catalogue. No-op on auto-cache providers (OpenAI, DeepSeek).
        cache_last_message: When True AND ``messages`` non-empty,
            providers that support explicit cache markers emit a
            breakpoint at the end of the last message — this is the
            "growing cache" pattern that gives subsequent turns a hit
            on everything up to and including this turn.
    """

    model: str
    messages: list[Message]
    system: str | list[SystemPromptSegment] | None = None
    tools: list[ToolDefinition] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    max_tokens: int = 4096
    temperature: float | None = None
    stop_sequences: list[str] | None = None
    cache_tools: bool = False
    cache_last_message: bool = False


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
