"""OpenRouter provider adapter.

OpenRouter exposes an OpenAI-compatible API but has its own identity headers,
model routing semantics, and error wrapping. This adapter reuses the OpenAI
message/tool formatting functions but handles OpenRouter-specific concerns.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import openai

from cairn.domain._content import TextBlock
from cairn.domain._provider import (
    MessageStop,
    ProviderEvent,
    TextDelta,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    UsageEvent,
)
from cairn.providers._openai import format_messages, format_tools
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
    from cairn.domain._provider import ProviderRequest

logger = logging.getLogger(__name__)

# Default headers for OpenRouter app identification
_DEFAULT_REFERER = "https://github.com/wiktordepina/cairn"
_DEFAULT_TITLE = "cairn"


class OpenRouterProvider:
    """OpenRouter API adapter implementing the Provider protocol.

    Uses the OpenAI SDK under the hood (OpenRouter is OpenAI-compatible)
    with OpenRouter-specific headers and error handling.
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

        Identical to OpenAI mapping — OpenRouter uses the same format.
        """
        results: list[ProviderEvent] = []

        if chunk.usage:
            results.append(
                UsageEvent(
                    input_tokens=chunk.usage.prompt_tokens or 0,
                    output_tokens=chunk.usage.completion_tokens or 0,
                )
            )

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
        try:
            import tiktoken

            enc = tiktoken.encoding_for_model(request.model)
        except (KeyError, ValueError):
            # OpenRouter model IDs are often like "anthropic/claude-3.5-sonnet"
            # which tiktoken won't know — fall back to estimation
            text = request.system or ""
            for msg in request.messages:
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text += block.text
            return len(text) // 4

        total = 0
        if request.system:
            total += len(enc.encode(request.system))
        for msg in request.messages:
            total += 4
            for block in msg.content:
                if isinstance(block, TextBlock):
                    total += len(enc.encode(block.text))
        return total
