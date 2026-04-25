"""OpenRouter provider adapter.

OpenRouter exposes an OpenAI-compatible API but has its own identity headers,
model routing semantics, and error wrapping. It also supports Anthropic-style
``cache_control`` markers for Anthropic-backed routes (silently ignored on
backends that don't support them) and returns cache-usage stats in either
Anthropic shape (``cache_creation_input_tokens`` /
``cache_read_input_tokens``) or OpenAI shape (``prompt_tokens_details.cached_tokens``)
depending on the backend.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import openai

from cairn.domain._content import ImageBlock, TextBlock, ToolResultBlock, ToolUseBlock
from cairn.domain._provider import (
    MessageStop,
    ProviderEvent,
    SystemPromptSegment,
    TextDelta,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    UsageEvent,
)
from cairn.providers._openai import (
    _serialize_tool_input,  # noqa: PLC2701  # pyright: ignore[reportPrivateUsage]
    flatten_system,
)
from cairn.providers._openai import (
    format_messages as _openai_format_messages,
)
from cairn.providers._openai import (
    format_tools as _openai_format_tools,
)
from cairn.providers._protocol import (
    AuthenticationError,
    ModelNotAvailableError,
    ProviderOverloadedError,
    RateLimitError,
)
from cairn.providers._translate import map_openai_stop_reason

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from cairn.config._models import ProviderConfig
    from cairn.config._secrets import SecretResolver
    from cairn.domain._messages import Message
    from cairn.domain._provider import ProviderRequest, ToolDefinition

logger = logging.getLogger(__name__)

# Default headers for OpenRouter app identification
_DEFAULT_REFERER = "https://github.com/wiktordepina/cairn"
_DEFAULT_TITLE = "cairn"

_CACHE_CONTROL_EPHEMERAL: dict[str, Any] = {"type": "ephemeral"}
_MAX_CACHE_MARKERS = 4


def _request_uses_cache(request: ProviderRequest) -> bool:
    """True iff the request asks for any cache marker placement."""
    if request.cache_tools or request.cache_last_message:
        return True
    return isinstance(request.system, list) and any(seg.cacheable for seg in request.system)


def _format_system_with_cache(
    system: str | list[SystemPromptSegment] | None,
) -> dict[str, Any] | None:
    """Translate the system prompt into an OpenAI-shaped ``role: "system"``
    message whose content is a list of text parts, with
    ``cache_control`` on cacheable segments."""
    if system is None:
        return None
    if isinstance(system, str):
        if not system:
            return None
        return {
            "role": "system",
            "content": [{"type": "text", "text": system}],
        }
    parts: list[dict[str, Any]] = []
    for seg in system:
        if not seg.text:
            continue
        part: dict[str, Any] = {"type": "text", "text": seg.text}
        if seg.cacheable:
            part["cache_control"] = _CACHE_CONTROL_EPHEMERAL
        parts.append(part)
    if not parts:
        return None
    return {"role": "system", "content": parts}


def _format_tools_with_cache(
    tools: list[ToolDefinition],
    *,
    cache_last: bool,
) -> list[dict[str, Any]]:
    """Format tools, optionally marking the last tool for caching."""
    formatted = _openai_format_tools(tools)
    if cache_last and formatted:
        formatted[-1]["cache_control"] = _CACHE_CONTROL_EPHEMERAL
    return formatted


def _format_messages_with_cache(
    messages: list[Message],
    *,
    cache_last: bool,
) -> list[dict[str, Any]]:
    """OpenAI-shaped messages with ``cache_control`` on the last text
    part of the last message when ``cache_last`` is True.

    Built from scratch (not via the OpenAI helper) because OpenRouter
    needs the user/assistant ``content`` to remain a list-of-parts even
    in the single-text case so a cache marker can attach.
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        tool_uses = [b for b in msg.content if isinstance(b, ToolUseBlock)]
        tool_results = [b for b in msg.content if isinstance(b, ToolResultBlock)]

        if tool_results:
            for tr in tool_results:
                content = tr.content if isinstance(tr.content, str) else str(tr.content)
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": tr.tool_use_id,
                        "content": content,
                    }
                )
            continue

        content_parts: list[dict[str, Any]] = []
        for block in msg.content:
            match block:
                case TextBlock():
                    content_parts.append({"type": "text", "text": block.text})
                case ImageBlock():
                    data_url = f"data:{block.source.media_type};base64,{block.source.data}"
                    content_parts.append({"type": "image_url", "image_url": {"url": data_url}})
                case ToolUseBlock() | ToolResultBlock():
                    pass
                case _:
                    pass

        msg_dict: dict[str, Any] = {"role": msg.role}
        if content_parts:
            msg_dict["content"] = content_parts
        else:
            msg_dict["content"] = None

        if tool_uses and msg.role == "assistant":
            msg_dict["tool_calls"] = [
                {
                    "id": tu.id,
                    "type": "function",
                    "function": {
                        "name": tu.name,
                        "arguments": _serialize_tool_input(tu.input),
                    },
                }
                for tu in tool_uses
            ]

        result.append(msg_dict)

    if cache_last and result:
        # Anchor cache_control on the last text-shaped content part of
        # the last message. If the last message is a tool result (string
        # content), anchor on the message itself via a sentinel — but
        # in practice the last message in a turn is always a user or
        # assistant message, so we keep this simple.
        last = result[-1]
        if isinstance(last.get("content"), list) and last["content"]:
            last["content"][-1]["cache_control"] = _CACHE_CONTROL_EPHEMERAL
    return result


def _count_cache_markers(
    *,
    system_msg: dict[str, Any] | None,
    tools: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> int:
    count = 0
    if system_msg is not None:
        sys_content = system_msg.get("content")
        if isinstance(sys_content, list):
            for part in sys_content:  # pyright: ignore[reportUnknownVariableType]
                if isinstance(part, dict) and "cache_control" in part:  # pyright: ignore[reportUnknownArgumentType]
                    count += 1
    count += sum(1 for t in tools if "cache_control" in t)
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:  # pyright: ignore[reportUnknownVariableType]
                if isinstance(part, dict) and "cache_control" in part:  # pyright: ignore[reportUnknownArgumentType]
                    count += 1
    return count


def _enforce_marker_cap(
    *,
    system_msg: dict[str, Any] | None,
    tools: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    provider_name: str,
) -> None:
    """Drop excess markers (oldest first) when the request body
    exceeds the 4-marker cap, mirroring the Anthropic adapter."""
    over = (
        _count_cache_markers(system_msg=system_msg, tools=tools, messages=messages)
        - _MAX_CACHE_MARKERS
    )
    if over <= 0:
        return
    logger.warning(
        "cache markers (%d) exceed cap %d; dropping oldest %d",
        over + _MAX_CACHE_MARKERS,
        _MAX_CACHE_MARKERS,
        over,
        extra={"provider": provider_name},
    )
    if system_msg is not None:
        sys_content = system_msg.get("content")
        if isinstance(sys_content, list):
            for part in sys_content:  # pyright: ignore[reportUnknownVariableType]
                if over <= 0:
                    return
                if isinstance(part, dict) and part.pop("cache_control", None) is not None:  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                    over -= 1
    for t in tools:
        if over <= 0:
            return
        if t.pop("cache_control", None) is not None:
            over -= 1
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:  # pyright: ignore[reportUnknownVariableType]
            if over <= 0:
                return
            if isinstance(part, dict) and part.pop("cache_control", None) is not None:  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                over -= 1


def _usage_from_chunk(usage: Any) -> UsageEvent:
    """Extract usage from an OpenRouter streamed-usage payload.

    OpenRouter forwards either Anthropic-shape
    (``cache_creation_input_tokens`` / ``cache_read_input_tokens``)
    OR OpenAI-shape (``prompt_tokens_details.cached_tokens``)
    depending on the routed backend. Anthropic-shape wins when both
    are present (defensive).
    """
    cache_read = 0
    cache_write = 0
    anthropic_read = getattr(usage, "cache_read_input_tokens", None)
    anthropic_write = getattr(usage, "cache_creation_input_tokens", None)
    if anthropic_read is not None or anthropic_write is not None:
        cache_read = anthropic_read or 0
        cache_write = anthropic_write or 0
    else:
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cache_read = getattr(details, "cached_tokens", 0) or 0
    return UsageEvent(
        input_tokens=(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=(getattr(usage, "completion_tokens", 0) or 0),
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )


class OpenRouterProvider:
    """OpenRouter API adapter implementing the Provider protocol.

    Uses the OpenAI SDK under the hood (OpenRouter is OpenAI-compatible)
    with OpenRouter-specific headers and error handling. Anthropic-style
    cache markers are emitted symmetrically — they're a no-op on
    non-supporting backends and pay off on Anthropic-routed models.
    """

    def __init__(self, config: ProviderConfig, secret_resolver: SecretResolver) -> None:
        self._config = config
        self._secret_resolver = secret_resolver
        self._client: openai.AsyncOpenAI | None = None

    @property
    def name(self) -> str:
        return self._config.name

    def _build_headers(self) -> dict[str, str]:
        """Build headers including OpenRouter-specific identification."""
        headers = dict(self._config.extra_headers) if self._config.extra_headers else {}
        headers.setdefault("HTTP-Referer", _DEFAULT_REFERER)
        headers.setdefault("X-Title", _DEFAULT_TITLE)
        return headers

    async def _get_client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            api_key = (
                self._secret_resolver.resolve(self._config.api_key)
                if self._config.api_key
                else None
            )
            base_url = self._config.base_url or "https://openrouter.ai/api/v1"
            self._client = openai.AsyncOpenAI(
                api_key=api_key or "",
                base_url=base_url,
                default_headers=self._build_headers(),
            )
        return self._client

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        """Stream a completion from OpenRouter."""
        client = await self._get_client()
        if _request_uses_cache(request):
            messages = _format_messages_with_cache(
                request.messages, cache_last=request.cache_last_message
            )
            tools = _format_tools_with_cache(request.tools, cache_last=request.cache_tools)
            system_msg = _format_system_with_cache(request.system)
            _enforce_marker_cap(
                system_msg=system_msg,
                tools=tools,
                messages=messages,
                provider_name=self.name,
            )
            if system_msg is not None:
                messages = [system_msg, *messages]
        else:
            # Legacy / non-cache path — OpenAI-shaped, flat string system.
            messages = _openai_format_messages(request.messages, system=request.system)
            tools = _openai_format_tools(request.tools)

        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.stop_sequences:
            kwargs["stop"] = request.stop_sequences

        tool_ids_by_index: dict[int, str] = {}

        try:
            response = await client.chat.completions.create(**kwargs)  # pyright: ignore[reportUnknownVariableType]
            async for chunk in response:  # type: ignore[union-attr]
                for pe in self._map_chunk(chunk, tool_ids_by_index):
                    yield pe
        except openai.AuthenticationError as exc:
            raise AuthenticationError(str(exc), provider=self.name) from exc
        except openai.RateLimitError as exc:
            raise RateLimitError(str(exc), provider=self.name) from exc
        except openai.NotFoundError as exc:
            raise ModelNotAvailableError(str(exc), provider=self.name) from exc
        except openai.APIStatusError as exc:
            if exc.status_code in (503, 529):
                raise ProviderOverloadedError(str(exc), provider=self.name) from exc
            raise

    def _map_chunk(
        self,
        chunk: Any,
        tool_ids_by_index: dict[int, str],
    ) -> list[ProviderEvent]:
        """Map a ChatCompletionChunk to ProviderEvents.

        Identical to OpenAI mapping plus dual-shape cache-usage parsing.
        """
        results: list[ProviderEvent] = []

        if chunk.usage:
            results.append(_usage_from_chunk(chunk.usage))

        if not chunk.choices:
            return results

        choice = chunk.choices[0]
        delta = choice.delta

        if delta and delta.content:
            results.append(TextDelta(text=delta.content))

        if delta and delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index
                if tc.id:
                    tool_ids_by_index[idx] = tc.id
                    fn_name = tc.function.name if tc.function else ""
                    results.append(ToolCallStart(id=tc.id, name=fn_name or ""))
                if tc.function and tc.function.arguments:
                    tool_id = tool_ids_by_index.get(idx, "")
                    results.append(ToolCallDelta(id=tool_id, input_delta=tc.function.arguments))

        if choice.finish_reason:
            if choice.finish_reason == "tool_calls":
                for tool_id in tool_ids_by_index.values():
                    results.append(ToolCallEnd(id=tool_id))
                tool_ids_by_index.clear()
            results.append(MessageStop(stop_reason=map_openai_stop_reason(choice.finish_reason)))

        return results

    async def count_tokens(self, request: ProviderRequest) -> int:
        """Estimate token count — same strategy as OpenAI adapter."""
        flat_system = flatten_system(request.system) or ""
        try:
            import tiktoken

            enc = tiktoken.encoding_for_model(request.model)
        except (KeyError, ValueError):
            # OpenRouter model IDs are often like "anthropic/claude-3.5-sonnet"
            # which tiktoken won't know — fall back to estimation
            text = flat_system
            for msg in request.messages:
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text += block.text
            return len(text) // 4

        total = 0
        if flat_system:
            total += len(enc.encode(flat_system))
        for msg in request.messages:
            total += 4
            for block in msg.content:
                if isinstance(block, TextBlock):
                    total += len(enc.encode(block.text))
        return total
