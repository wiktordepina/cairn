"""OpenAI provider adapter.

Also serves OpenAI-compatible local servers (llama.cpp, vLLM, LM Studio)
via `base_url` in config.
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


# ---------------------------------------------------------------------------
# Message formatting (cairn → OpenAI SDK format)
# ---------------------------------------------------------------------------


def flatten_system(
    system: str | list[SystemPromptSegment] | None,
) -> str | None:
    """Collapse a `ProviderRequest.system` value to a flat string.

    OpenAI-shaped APIs (OpenAI itself, DeepSeek, and the
    OpenAI-compatible local-server path) take the system prompt as a
    single ``role: "system"`` message. They have no equivalent of
    Anthropic's per-segment cache markers — caching, where it exists,
    is automatic on stable prefixes. So we keep the segment text in
    arrival order and join with a blank line.
    """
    if system is None:
        return None
    if isinstance(system, str):
        return system
    return "\n\n".join(seg.text for seg in system if seg.text)


def format_messages(
    messages: list[Message],
    system: str | list[SystemPromptSegment] | None = None,
) -> list[dict[str, Any]]:
    """Convert cairn Messages to OpenAI message format.

    Key differences from Anthropic:
    - System prompt is a message with `role: "system"`
    - Tool uses go in a `tool_calls` array on the assistant message
    - Tool results become `role: "tool"` messages (one per result)

    A `SystemPromptSegment` list is flattened to a single string —
    OpenAI-shaped APIs cache automatically on stable prefixes and have
    no per-segment marker equivalent.
    """
    result: list[dict[str, Any]] = []

    flat_system = flatten_system(system)
    if flat_system:
        result.append({"role": "system", "content": flat_system})

    for msg in messages:
        tool_uses = [b for b in msg.content if isinstance(b, ToolUseBlock)]
        tool_results = [b for b in msg.content if isinstance(b, ToolResultBlock)]

        # Tool results become separate "tool" role messages
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

        # Build content parts
        content_parts: list[dict[str, Any]] = []
        for block in msg.content:
            match block:
                case TextBlock():
                    content_parts.append({"type": "text", "text": block.text})
                case ImageBlock():
                    data_url = f"data:{block.source.media_type};base64,{block.source.data}"
                    content_parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        }
                    )
                case ToolUseBlock() | ToolResultBlock():
                    pass  # handled separately
                case _:
                    pass  # skip unsupported blocks (e.g. ThinkingBlock)

        msg_dict: dict[str, Any] = {"role": msg.role}

        # Content: use string if only text, else list
        if content_parts:
            if len(content_parts) == 1 and content_parts[0]["type"] == "text":
                msg_dict["content"] = content_parts[0]["text"]
            else:
                msg_dict["content"] = content_parts
        else:
            msg_dict["content"] = None

        # Tool calls on assistant messages
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

    return result


def format_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    """Convert cairn ToolDefinitions to OpenAI tool format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


def _serialize_tool_input(input_dict: dict[str, Any]) -> str:
    """Serialize tool input to JSON string for OpenAI format."""
    import json

    return json.dumps(input_dict)


def _usage_from_chunk(usage: Any) -> UsageEvent:
    """Build a `UsageEvent` from an OpenAI streamed-usage payload.

    OpenAI auto-cache surfaces hits via
    ``usage.prompt_tokens_details.cached_tokens``. There is no
    distinct write-tokens field — the API does not separate cache
    creation from baseline input. We populate ``cache_read_tokens``
    only and leave ``cache_write_tokens`` at zero.

    Robust against missing or ``None`` attributes (older SDK versions,
    fixtures, OpenAI-compat local servers that don't echo cache
    fields).
    """
    cached_tokens = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached_tokens = getattr(details, "cached_tokens", 0) or 0
    return UsageEvent(
        input_tokens=(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=(getattr(usage, "completion_tokens", 0) or 0),
        cache_read_tokens=cached_tokens,
        cache_write_tokens=0,
    )


# ---------------------------------------------------------------------------
# Provider adapter
# ---------------------------------------------------------------------------


class OpenAIProvider:
    """OpenAI API adapter implementing the Provider protocol.

    Also works for any OpenAI-compatible server (llama.cpp, vLLM, LM Studio)
    via `base_url` in the provider config.
    """

    def __init__(self, config: ProviderConfig, secret_resolver: SecretResolver) -> None:
        self._config = config
        self._secret_resolver = secret_resolver
        self._client: openai.AsyncOpenAI | None = None

    @property
    def name(self) -> str:
        return self._config.name

    async def _get_client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            api_key = (
                self._secret_resolver.resolve(self._config.api_key)
                if self._config.api_key
                else "not-needed"  # local servers often don't need a key
            )
            self._client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url=self._config.base_url,
                default_headers=self._config.extra_headers or None,
            )
        return self._client

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        """Stream a completion from the OpenAI API."""
        client = await self._get_client()
        messages = format_messages(request.messages, system=request.system)
        tools = format_tools(request.tools)

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

        # Track tool call IDs by their streaming index
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
        """Map an OpenAI ChatCompletionChunk to zero or more ProviderEvents."""
        results: list[ProviderEvent] = []

        # Usage info (usually on the final chunk)
        if chunk.usage:
            results.append(_usage_from_chunk(chunk.usage))

        if not chunk.choices:
            return results

        choice = chunk.choices[0]
        delta = choice.delta

        # Text content
        if delta and delta.content:
            results.append(TextDelta(text=delta.content))

        # Tool calls
        if delta and delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index
                # First chunk for this tool call has the ID and name
                if tc.id:
                    tool_ids_by_index[idx] = tc.id
                    fn_name = tc.function.name if tc.function else ""
                    results.append(ToolCallStart(id=tc.id, name=fn_name or ""))

                # Subsequent chunks have argument deltas
                if tc.function and tc.function.arguments:
                    tool_id = tool_ids_by_index.get(idx, "")
                    results.append(ToolCallDelta(id=tool_id, input_delta=tc.function.arguments))

        # Finish reason
        if choice.finish_reason:
            # Emit ToolCallEnd for each tracked tool before MessageStop
            if choice.finish_reason == "tool_calls":
                for tool_id in tool_ids_by_index.values():
                    results.append(ToolCallEnd(id=tool_id))
                tool_ids_by_index.clear()

            results.append(MessageStop(stop_reason=map_openai_stop_reason(choice.finish_reason)))

        return results

    async def count_tokens(self, request: ProviderRequest) -> int:
        """Estimate token count using tiktoken.

        Falls back to character-based estimation for models not known
        to tiktoken (common with OpenAI-compatible local servers).
        """
        flat_system = flatten_system(request.system) or ""
        try:
            import tiktoken

            enc = tiktoken.encoding_for_model(request.model)
        except (KeyError, ValueError):
            # Model not known to tiktoken — rough estimation
            logger.warning(
                "tiktoken has no encoding for model %r, using character-based estimate",
                request.model,
            )
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
            total += 4  # per-message overhead
            for block in msg.content:
                if isinstance(block, TextBlock):
                    total += len(enc.encode(block.text))
        return total
