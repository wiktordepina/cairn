"""Anthropic provider adapter."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

import anthropic

from cairn.domain._content import (
    ImageBlock,
    RedactedThinkingBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from cairn.domain._provider import (
    BalanceInfo,
    MessageStop,
    ProviderEvent,
    SystemPromptSegment,
    TextDelta,
    ThinkingDelta,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    UsageEvent,
)
from cairn.providers._protocol import (
    AuthenticationError,
    ModelNotAvailableError,
    ProviderOverloadedError,
    RateLimitError,
)
from cairn.providers._translate import map_anthropic_stop_reason

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from cairn.config._models import ProviderConfig
    from cairn.config._secrets import SecretResolver
    from cairn.domain._messages import Message
    from cairn.domain._provider import ProviderRequest, ToolDefinition

logger = logging.getLogger(__name__)


# Anthropic caps cache_control markers at 4 per request. We enforce
# this defensively in the adapter so a misconfigured upstream caller
# never propagates a 400 to the model layer. Verified 2026-04 against
# Anthropic's prompt-caching docs.
_MAX_CACHE_MARKERS = 4
_CACHE_CONTROL_EPHEMERAL: dict[str, Any] = {"type": "ephemeral"}

# Anthropic's documented minimum for thinking.budget_tokens.
_MIN_THINKING_BUDGET = 1024

# Proportional mapping of unified reasoning_effort to fraction of
# request.max_tokens (ADR 0043). ``none`` and ``minimal`` skip
# thinking entirely and are absent from this map.
_EFFORT_BUDGET_FRACTION: dict[str, float] = {
    "low": 0.25,
    "medium": 0.50,
    "high": 0.75,
    "xhigh": 0.90,
}


# ---------------------------------------------------------------------------
# Message formatting (cairn → Anthropic SDK format)
# ---------------------------------------------------------------------------


def _format_content_block(block: Any) -> dict[str, Any]:
    """Convert a single cairn ContentBlock to Anthropic dict format."""
    match block:
        case TextBlock():
            return {"type": "text", "text": block.text}
        case ToolUseBlock():
            return {
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            }
        case ToolResultBlock():
            content: str | list[dict[str, Any]]
            if isinstance(block.content, list):
                content = [_format_content_block(b) for b in block.content]
            else:
                content = block.content
            return {
                "type": "tool_result",
                "tool_use_id": block.tool_use_id,
                "content": content,
                "is_error": block.is_error,
            }
        case ImageBlock():
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": block.source.media_type,
                    "data": block.source.data,
                },
            }
        case ThinkingBlock():
            # ``signature`` MUST be sent back when the prior turn included
            # tool_use; the server decrypts it to reconstruct the original
            # reasoning. Empty string would 400; omit the field instead.
            payload: dict[str, Any] = {"type": "thinking", "thinking": block.thinking}
            if block.signature:
                payload["signature"] = block.signature
            return payload
        case RedactedThinkingBlock():
            return {"type": "redacted_thinking", "data": block.data}
        case _:
            return {"type": "text", "text": str(block)}


def format_messages(
    messages: list[Message],
    *,
    cache_last: bool = False,
) -> list[dict[str, Any]]:
    """Convert cairn Messages to Anthropic message format.

    When ``cache_last`` is True and ``messages`` is non-empty, an
    ephemeral ``cache_control`` marker is set on the LAST content block
    of the LAST message — Anthropic's "growing cache" pattern. The
    next turn's request gets a cache hit on everything up to and
    including this point.
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        content: list[dict[str, Any]] = [_format_content_block(b) for b in msg.content]
        result.append({"role": msg.role, "content": content})
    if cache_last and result and result[-1]["content"]:
        result[-1]["content"][-1]["cache_control"] = _CACHE_CONTROL_EPHEMERAL
    return result


def format_tools(
    tools: list[ToolDefinition],
    *,
    cache_last: bool = False,
) -> list[dict[str, Any]]:
    """Convert cairn ToolDefinitions to Anthropic tool format.

    When ``cache_last`` is True and ``tools`` is non-empty, the LAST
    tool gets an ephemeral ``cache_control`` marker so the whole tool
    catalogue prefix is cached.
    """
    formatted = [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in tools
    ]
    if cache_last and formatted:
        formatted[-1]["cache_control"] = _CACHE_CONTROL_EPHEMERAL
    return formatted


def format_system(
    system: str | list[SystemPromptSegment] | None,
) -> str | list[dict[str, Any]] | None:
    """Translate `ProviderRequest.system` to the Anthropic ``system`` param.

    A plain string passes through unchanged (legacy path). A segment
    list maps to the Anthropic ``list[{"type": "text", ...}]`` form
    with ``cache_control`` on segments whose ``cacheable`` is True.
    """
    if system is None:
        return None
    if isinstance(system, str):
        return system
    blocks: list[dict[str, Any]] = []
    for seg in system:
        block: dict[str, Any] = {"type": "text", "text": seg.text}
        if seg.cacheable:
            block["cache_control"] = _CACHE_CONTROL_EPHEMERAL
        blocks.append(block)
    return blocks


def _count_cache_markers(
    *,
    system: str | list[dict[str, Any]] | None,
    tools: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> int:
    """Count cache_control markers across the formatted request body."""
    count = 0
    if isinstance(system, list):
        count += sum(1 for blk in system if "cache_control" in blk)
    count += sum(1 for t in tools if "cache_control" in t)
    for msg in messages:
        for blk in msg.get("content", []):
            if isinstance(blk, dict) and "cache_control" in blk:
                count += 1
    return count


def _format_tool_choice(
    tool_choice: str | tuple[str, str] | None,
    *,
    disable_parallel: bool,
    thinking_on: bool,
    provider_name: str,
) -> dict[str, Any] | None:
    """Translate the unified ``tool_choice`` shape to Anthropic's payload.

    Anthropic accepts ``{"type": "auto" | "any" | "none" | "tool", "name": ...}``
    plus an optional ``disable_parallel_tool_use`` on ``auto`` / ``any`` /
    ``tool``. When extended thinking is on, only ``auto`` and ``none``
    are supported — ``any`` and ``tool`` would 400. We downgrade with a
    WARNING rather than blowing up the request.
    """
    if tool_choice is None and not disable_parallel:
        return None
    if tool_choice is None:
        # parallel-disable only — needs a type: "auto" wrapper to attach to.
        choice_type = "auto"
        name: str | None = None
    elif isinstance(tool_choice, tuple):
        choice_type, name = tool_choice
    else:
        choice_type = tool_choice
        name = None

    if thinking_on and choice_type in ("any", "tool"):
        logger.warning(
            "tool_choice=%r incompatible with extended thinking; downgrading to 'auto'",
            choice_type,
            extra={"provider": provider_name},
        )
        choice_type = "auto"
        name = None

    payload: dict[str, Any] = {"type": choice_type}
    if choice_type == "tool" and name:
        payload["name"] = name
    if disable_parallel and choice_type in ("auto", "any", "tool"):
        payload["disable_parallel_tool_use"] = True
    return payload


def _resolve_thinking_budget(
    effort: str | None,
    *,
    max_tokens: int,
) -> int | None:
    """Map unified ``reasoning_effort`` → Anthropic ``thinking.budget_tokens``.

    Returns ``None`` for ``None`` / ``"none"`` / ``"minimal"`` (no
    thinking block). Otherwise scales proportionally to ``max_tokens``,
    floored at ``_MIN_THINKING_BUDGET`` and ceilinged at
    ``max_tokens - 1`` (the server requires
    ``budget_tokens < max_tokens``). See ADR 0043.
    """
    if effort is None or effort in ("none", "minimal"):
        return None
    fraction = _EFFORT_BUDGET_FRACTION.get(effort)
    if fraction is None:
        logger.warning("unknown reasoning_effort %r; ignoring", effort)
        return None
    budget = int(max_tokens * fraction)
    budget = max(budget, _MIN_THINKING_BUDGET)
    budget = min(budget, max_tokens - 1)
    if budget < _MIN_THINKING_BUDGET:
        # max_tokens itself was below the floor — no thinking possible.
        return None
    return budget


def _enforce_marker_cap(
    *,
    system: str | list[dict[str, Any]] | None,
    tools: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    provider_name: str,
) -> None:
    """Drop excess markers (oldest first) when the request body
    exceeds Anthropic's 4-marker cap.

    The "oldest first" policy keeps the most-recent breakpoint (the
    growing tail) — that's the one that pays off most on the next
    turn. Logs a WARNING with the provider so operators notice
    upstream miscoding.
    """
    over = _count_cache_markers(system=system, tools=tools, messages=messages) - _MAX_CACHE_MARKERS
    if over <= 0:
        return
    logger.warning(
        "anthropic-style cache markers (%d) exceed cap %d; dropping oldest %d",
        over + _MAX_CACHE_MARKERS,
        _MAX_CACHE_MARKERS,
        over,
        extra={"provider": provider_name},
    )
    # Drop from system first, then tools, then earliest message.
    if isinstance(system, list):
        for blk in system:
            if over <= 0:
                return
            if blk.pop("cache_control", None) is not None:
                over -= 1
    for t in tools:
        if over <= 0:
            return
        if t.pop("cache_control", None) is not None:
            over -= 1
    for msg in messages:
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for blk in content:  # pyright: ignore[reportUnknownVariableType]
            if over <= 0:
                return
            if isinstance(blk, dict) and blk.pop("cache_control", None) is not None:  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                over -= 1


# ---------------------------------------------------------------------------
# Provider adapter
# ---------------------------------------------------------------------------


class AnthropicProvider:
    """Anthropic API adapter implementing the Provider protocol."""

    def __init__(self, config: ProviderConfig, secret_resolver: SecretResolver) -> None:
        self._config = config
        self._secret_resolver = secret_resolver
        self._client: anthropic.AsyncAnthropic | None = None

    @property
    def name(self) -> str:
        return self._config.name

    async def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            api_key = (
                self._secret_resolver.resolve(self._config.api_key)
                if self._config.api_key
                else None
            )
            self._client = anthropic.AsyncAnthropic(
                api_key=api_key,
                base_url=self._config.base_url,
                default_headers=self._config.extra_headers or None,
            )
        return self._client

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        """Stream a completion from the Anthropic API."""
        client = await self._get_client()
        messages = format_messages(request.messages, cache_last=request.cache_last_message)
        tools = format_tools(request.tools, cache_last=request.cache_tools)
        system = format_system(request.system)
        _enforce_marker_cap(system=system, tools=tools, messages=messages, provider_name=self.name)

        thinking_budget = _resolve_thinking_budget(
            request.reasoning_effort, max_tokens=request.max_tokens
        )
        thinking_on = thinking_budget is not None

        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
        }
        if system is not None:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.stop_sequences:
            kwargs["stop_sequences"] = request.stop_sequences
        if thinking_budget is not None:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
        tool_choice = _format_tool_choice(
            request.tool_choice,
            disable_parallel=request.disable_parallel_tool_use,
            thinking_on=thinking_on,
            provider_name=self.name,
        )
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice

        # Track which content block index maps to which tool_use id and
        # which is a thinking block (so we can route deltas correctly).
        block_id_by_index: dict[int, str] = {}
        thinking_indices: set[int] = set()
        # Single-emit usage accumulator (Anthropic streams partial usage
        # at message_start and the final running total at message_delta;
        # we collapse to one UsageEvent at message_stop to match
        # OpenAI / DeepSeek and avoid double-counting downstream).
        usage_acc: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }

        try:
            async with client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    for pe in self._map_event(
                        event, block_id_by_index, thinking_indices, usage_acc
                    ):
                        yield pe
        except anthropic.AuthenticationError as exc:
            raise AuthenticationError(str(exc), provider=self.name) from exc
        except anthropic.RateLimitError as exc:
            retry_after = None
            if hasattr(exc, "response") and exc.response is not None:  # pyright: ignore[reportUnnecessaryComparison]
                retry_header = exc.response.headers.get("retry-after")
                if retry_header:
                    with contextlib.suppress(ValueError):
                        retry_after = float(retry_header)
            raise RateLimitError(str(exc), provider=self.name, retry_after=retry_after) from exc
        except anthropic.NotFoundError as exc:
            raise ModelNotAvailableError(str(exc), provider=self.name) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code == 529:
                raise ProviderOverloadedError(str(exc), provider=self.name) from exc
            raise

    def _map_event(
        self,
        event: Any,
        block_id_by_index: dict[int, str],
        thinking_indices: set[int],
        usage_acc: dict[str, int],
    ) -> list[ProviderEvent]:
        """Map an Anthropic SDK event to zero or more ProviderEvents.

        Usage handling: ``message_start`` carries a partial usage
        breakdown (input + cache + a small initial output). The
        single ``message_delta`` event carries the *cumulative* final
        ``output_tokens``. Emitting both as separate ``UsageEvent``s
        previously caused the orchestrator to write two model_usage
        rows that got SUMmed downstream, over-counting ``output_tokens``
        by the message_start baseline. We now accumulate in
        ``usage_acc`` and emit one ``UsageEvent`` at ``message_stop``,
        matching the OpenAI and DeepSeek single-emit pattern.

        Thinking handling: ``content_block_start`` with type
        ``thinking`` opens a thinking block; subsequent
        ``content_block_delta`` events carry either ``thinking_delta``
        (display text) or ``signature_delta`` (the encrypted handle
        required for multi-turn tool-use round-trip). Type
        ``redacted_thinking`` arrives complete in a single
        ``content_block_start`` and is logged-and-skipped for V1.
        """
        results: list[ProviderEvent] = []
        event_type = getattr(event, "type", None)

        match event_type:
            case "message_start":
                usage = event.message.usage
                usage_acc["input_tokens"] = usage.input_tokens or 0
                usage_acc["output_tokens"] = usage.output_tokens or 0
                usage_acc["cache_read_tokens"] = getattr(usage, "cache_read_input_tokens", 0) or 0
                usage_acc["cache_write_tokens"] = (
                    getattr(usage, "cache_creation_input_tokens", 0) or 0
                )

            case "content_block_start":
                block = event.content_block
                if block.type == "tool_use":
                    block_id_by_index[event.index] = block.id
                    results.append(ToolCallStart(id=block.id, name=block.name))
                elif block.type == "thinking":
                    thinking_indices.add(event.index)
                    # Some SDKs include the signature on the start event
                    # (single-shot non-streamed thinking). Forward it.
                    initial_sig = getattr(block, "signature", "") or ""
                    initial_text = getattr(block, "thinking", "") or ""
                    if initial_text or initial_sig:
                        results.append(ThinkingDelta(text=initial_text, signature=initial_sig))
                elif block.type == "redacted_thinking":
                    logger.warning(
                        "anthropic returned a redacted_thinking block; dropping "
                        "(persistence deferred — see RedactedThinkingBlock)",
                        extra={"provider": self.name},
                    )

            case "content_block_delta":
                delta = event.delta
                if delta.type == "text_delta":
                    results.append(TextDelta(text=delta.text))
                elif delta.type == "input_json_delta":
                    block_id = block_id_by_index.get(event.index, "")
                    results.append(ToolCallDelta(id=block_id, input_delta=delta.partial_json))
                elif delta.type == "thinking_delta":
                    results.append(ThinkingDelta(text=delta.thinking))
                elif delta.type == "signature_delta":
                    results.append(ThinkingDelta(text="", signature=delta.signature))

            case "content_block_stop":
                if event.index in thinking_indices:
                    thinking_indices.discard(event.index)
                else:
                    block_id = block_id_by_index.pop(event.index, None)
                    if block_id is not None:
                        results.append(ToolCallEnd(id=block_id))

            case "message_delta":
                usage = getattr(event, "usage", None)
                if usage:
                    # Cumulative running total — overwrite, don't add.
                    usage_acc["output_tokens"] = usage.output_tokens or usage_acc["output_tokens"]
                stop_reason = getattr(event.delta, "stop_reason", None)
                if stop_reason:
                    # Emit the single accumulated usage event before the stop
                    # so downstream sees them in stream-natural order.
                    results.append(
                        UsageEvent(
                            input_tokens=usage_acc["input_tokens"],
                            output_tokens=usage_acc["output_tokens"],
                            cache_read_tokens=usage_acc["cache_read_tokens"],
                            cache_write_tokens=usage_acc["cache_write_tokens"],
                        )
                    )
                    results.append(MessageStop(stop_reason=map_anthropic_stop_reason(stop_reason)))

            case _:
                pass  # ignore unknown event types

        return results

    async def count_tokens(self, request: ProviderRequest) -> int:
        """Count tokens using the Anthropic SDK's native endpoint."""
        client = await self._get_client()
        messages = format_messages(request.messages)
        tools = format_tools(request.tools)
        system = format_system(request.system)

        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
        }
        if system is not None:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        result = await client.messages.count_tokens(**kwargs)
        return result.input_tokens

    async def balance(self) -> BalanceInfo | None:
        """Anthropic exposes no public balance endpoint reachable with the
        Messages-API key — the Admin Cost Report API requires a separate
        admin key. Returns ``None``; the ``cairn balance`` CLI surfaces
        this as a "no public API" stub row.
        """
        return None
