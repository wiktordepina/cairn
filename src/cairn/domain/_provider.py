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
    reasoning_effort: str | None = None
    """Reasoning depth knob, unified across thinking-capable providers.

    Accepted values: ``"none"``, ``"minimal"``, ``"low"``, ``"medium"``,
    ``"high"``, ``"xhigh"``. Adapter-specific mapping (see ADR 0043):

    - **OpenAI**: passed through verbatim to the ``reasoning_effort``
      param on o-series models; ignored on non-reasoning models.
    - **Anthropic**: scaled proportionally to ``max_tokens`` and emitted
      as ``thinking.budget_tokens`` (low/medium/high/xhigh = 25/50/75/90 %,
      floored at 1024, ceilinged at ``max_tokens - 1``). ``none`` /
      ``minimal`` skip the thinking block entirely. Only applied when
      the model declares ``supports_thinking``.
    - **DeepSeek**: forwarded as ``reasoning_effort`` on V4-pro-class
      models; no-op on ``deepseek-reasoner`` (always thinks).
    - **OpenRouter**: best-effort passthrough — translation depends on
      whichever upstream the route resolves to.
    """
    tool_choice: str | tuple[str, str] | None = None
    """Tool-selection strategy.

    ``None`` = adapter default (typically ``"auto"`` when tools present).
    String values: ``"auto"``, ``"any"``, ``"none"``. To force a
    specific tool, pass a tuple ``("tool", tool_name)``. Adapters that
    don't natively support all values translate or warn-and-fall-back.
    """
    disable_parallel_tool_use: bool = False
    """When True, instruct the provider to call tools sequentially.

    Honoured by Anthropic (validated against the extended-thinking
    constraint that only ``"auto"`` and ``"none"`` are supported when
    thinking is on) and OpenAI (via ``parallel_tool_calls=False``).
    Ignored on adapters without an equivalent knob.
    """
    prompt_cache_key: str | None = None
    """Stable key for cache-prefix partitioning.

    Currently consumed by the OpenAI adapter — the SDK doc describes
    it as "used by OpenAI to cache responses for similar requests to
    optimize your cache hit rates" and as the replacement for the
    legacy ``user`` field. Typically set to ``f"cairn:{session_id}"``
    by the orchestrator. Ignored on adapters without an equivalent.
    """


# ---------------------------------------------------------------------------
# Provider events — streamed from Provider.stream()
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextDelta:
    """Incremental text token from the model."""

    text: str


@dataclass(frozen=True, slots=True)
class ThinkingDelta:
    """Incremental reasoning / chain-of-thought token from the model.

    Emitted by providers that surface reasoning traces alongside the
    final answer. Two flavours of payload share this event:

    - ``text``: incremental reasoning content (DeepSeek's
      ``reasoning_content``, Anthropic's ``thinking_delta.thinking``).
    - ``signature``: Anthropic's encrypted handle for the trace,
      arriving once at end-of-thinking-block via ``signature_delta``.
      Empty on providers that don't sign their reasoning. Required on
      Anthropic multi-turn tool-use loops — the server decrypts it to
      reconstruct the original reasoning.

    A single delta carries one or the other, not both.
    """

    text: str
    signature: str = ""


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
    """Token consumption from a provider call.

    ``reasoning_tokens`` is a *breakdown* of ``output_tokens`` (already
    counted in the total) — it lets cost meters explain why a turn
    was expensive on reasoning-class models. ``cache_discount`` is the
    USD discount already applied by the provider for cache hits;
    surfaced uniformly by OpenRouter, zero on others.
    """

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cache_discount_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class GenerationId:
    """Provider-native identifier for a single completion.

    Emitted at most once per stream by adapters that return one.
    OpenRouter uses it to look up authoritative cost via
    ``GET /api/v1/generation?id=<id>``; Anthropic and OpenAI also
    return native IDs (``message.id``, completion ``id``) which can
    populate this event in future. Persisted on the assistant message
    so post-hoc accounting can reconcile.
    """

    id: str


@dataclass(frozen=True, slots=True)
class MessageStop:
    """The model has finished generating."""

    stop_reason: StopReason


ProviderEvent = (
    TextDelta
    | ThinkingDelta
    | ToolCallStart
    | ToolCallDelta
    | ToolCallEnd
    | UsageEvent
    | GenerationId
    | MessageStop
)


@dataclass(frozen=True, slots=True)
class BalanceInfo:
    """Account balance / credit state from a provider's billing API.

    Attributes:
        currency: ISO-ish currency code (``"USD"``, ``"CNY"``, …).
        total: Combined granted + topped-up credit.
        used: Spend to date in the same currency.
        remaining: ``total - used``. Stored explicitly because some
            APIs return it directly without exposing the components.
        granted: Promotional / unexpired grant component, or ``None``.
        source: Endpoint or API path the figure came from, for the
            CLI to attribute the row.
    """

    currency: str
    total: float
    used: float
    remaining: float
    granted: float | None = None
    source: str = ""
