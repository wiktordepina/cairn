"""DeepSeek provider adapter.

DeepSeek ships an OpenAI-compatible API at ``https://api.deepseek.com/v1``,
with reasoning- and chat-class models:

- ``deepseek-chat`` — general-purpose model, tool-use capable.
- ``deepseek-reasoner`` (and the V4-class thinking SKUs) — stream
  reasoning traces alongside the final answer. ``reasoning_content``
  on streamed deltas is captured into a :class:`ThinkingBlock` on the
  assistant message and re-emitted on the next turn under the
  ``reasoning_content`` field. Per DeepSeek's docs, this round-trip is
  *required* when the prior assistant turn contained tool calls and
  *optional but ignored* otherwise — so we always send it for
  simplicity and correctness across both shapes. The trace is
  round-tripped invisibly; surfacing it in the transcript is a
  follow-up.

Caching is automatic and disk-based — no markers, no opt-in. Cache
hits are billed at roughly 10 % of the input rate. Usage echoes back
``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens`` (note the
``_hit`` / ``_miss`` suffix, distinct from OpenAI's ``cached_tokens``);
we surface the hit count via :attr:`UsageEvent.cache_read_tokens`.
``cache_write_tokens`` stays at zero — DeepSeek does not separate
cache-creation from baseline input tokens.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import openai

from cairn.domain._content import TextBlock, ThinkingBlock
from cairn.domain._provider import (
    BalanceInfo,
    MessageStop,
    ProviderEvent,
    TextDelta,
    ThinkingDelta,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    UsageEvent,
)
from cairn.providers._openai import (
    flatten_system,
    format_tools,
)
from cairn.providers._openai import (
    format_messages as _openai_format_messages,
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
    from cairn.domain._provider import ProviderRequest, SystemPromptSegment

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.deepseek.com/v1"


def format_messages(
    messages: list[Message],
    system: str | list[SystemPromptSegment] | None = None,
) -> list[dict[str, Any]]:
    """OpenAI-shaped formatter with DeepSeek ``reasoning_content``.

    Identical to :func:`cairn.providers._openai.format_messages` plus a
    per-assistant-message ``reasoning_content`` field re-emitted from
    the message's leading :class:`ThinkingBlock`. Per DeepSeek's
    thinking-mode docs, replaying ``reasoning_content`` is *required*
    when the prior assistant turn included tool calls and *optional
    (silently ignored)* otherwise — we send it unconditionally to keep
    the formatter rule-free and safe across both shapes.
    """
    formatted = _openai_format_messages(messages, system=system)
    cairn_assistants = [m for m in messages if m.role == "assistant"]
    formatted_assistants = [r for r in formatted if r.get("role") == "assistant"]
    for cairn_msg, row in zip(cairn_assistants, formatted_assistants, strict=False):
        if not cairn_msg.content:
            continue
        head = cairn_msg.content[0]
        if isinstance(head, ThinkingBlock) and head.thinking:
            row["reasoning_content"] = head.thinking
    return formatted


def _usage_from_chunk(usage: Any) -> UsageEvent:
    """Build a :class:`UsageEvent` from a DeepSeek usage payload.

    DeepSeek splits the prompt-tokens count into
    ``prompt_cache_hit_tokens`` (cached) + ``prompt_cache_miss_tokens``
    (fresh). Their sum equals ``prompt_tokens``. We surface the hit
    count as ``cache_read_tokens``; the miss count is informational
    and not stored separately. ``cache_write_tokens`` is always zero.

    Robust against missing or ``None`` attributes — fixtures and older
    SDK releases may omit cache fields.
    """
    return UsageEvent(
        input_tokens=(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=(getattr(usage, "completion_tokens", 0) or 0),
        cache_read_tokens=(getattr(usage, "prompt_cache_hit_tokens", 0) or 0),
        cache_write_tokens=0,
    )


class DeepSeekProvider:
    """DeepSeek API adapter implementing the Provider protocol.

    Reuses the OpenAI SDK (DeepSeek is OpenAI-compatible) pointed at
    ``https://api.deepseek.com/v1``. Auto-cache only — cache flags on
    :class:`ProviderRequest` are no-ops here, mirroring OpenAI's
    posture.
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
                else None
            )
            base_url = self._config.base_url or _DEFAULT_BASE_URL
            self._client = openai.AsyncOpenAI(
                api_key=api_key or "",
                base_url=base_url,
                default_headers=self._config.extra_headers or None,
            )
        return self._client

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        """Stream a completion from the DeepSeek API."""
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
        """Map a DeepSeek ChatCompletionChunk to ProviderEvents.

        Identical to OpenAI mapping plus DeepSeek-specific shapes:

        - ``reasoning_content`` on the delta is captured into a
          :class:`ThinkingDelta` so the orchestrator can persist it on
          the assistant message and round-trip it on the next turn.
        - DeepSeek's ``prompt_cache_hit_tokens`` → ``cache_read_tokens``.
        """
        results: list[ProviderEvent] = []

        if chunk.usage:
            results.append(_usage_from_chunk(chunk.usage))

        if not chunk.choices:
            return results

        choice = chunk.choices[0]
        delta = choice.delta

        if delta:
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                results.append(ThinkingDelta(text=reasoning))

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
        """Estimate token count using tiktoken with character-fallback.

        DeepSeek does not publish a tokenizer compatible with tiktoken;
        we fall back to the character-based estimate for unknown models.
        """
        flat_system = flatten_system(request.system) or ""
        try:
            import tiktoken

            enc = tiktoken.encoding_for_model(request.model)
        except (KeyError, ValueError):
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

    async def balance(self) -> BalanceInfo | None:
        """Fetch DeepSeek account balance via ``GET /user/balance``.

        DeepSeek's response shape:

        .. code-block:: json

            {
              "is_available": true,
              "balance_infos": [
                {"currency": "CNY", "total_balance": "110.00",
                 "granted_balance": "10.00", "topped_up_balance": "100.00"}
              ]
            }

        Returns the FIRST entry in ``balance_infos`` — accounts with
        multi-currency balances are rare; the CLI can re-call when we
        need full per-currency listings. Returns ``None`` on any
        transport failure or malformed response so the CLI fan-out
        keeps going for other providers.
        """
        import httpx

        if self._config.api_key is None:
            return None
        api_key = self._secret_resolver.resolve(self._config.api_key)
        base_url = self._config.base_url or _DEFAULT_BASE_URL
        url = f"{base_url.rstrip('/')}/user/balance"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("deepseek balance fetch failed: %s", exc)
            return None
        if not isinstance(data, dict):
            return None
        data_dict: dict[str, Any] = data  # pyright: ignore[reportUnknownVariableType]
        infos = data_dict.get("balance_infos")
        if not isinstance(infos, list) or not infos:
            return None
        first_raw = infos[0]  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(first_raw, dict):
            return None
        first: dict[str, Any] = first_raw  # pyright: ignore[reportUnknownVariableType]
        try:
            total_balance = float(first.get("total_balance", 0) or 0)
            granted = float(first.get("granted_balance", 0) or 0)
        except (TypeError, ValueError):
            return None
        # DeepSeek's endpoint reports the CURRENT REMAINING balance only —
        # ``total_balance`` is granted + topped_up still available, with
        # no historical-spend field. We surface ``remaining`` faithfully
        # and leave ``used`` at 0.0 (unknown). The CLI's "used" column
        # is informational across providers; OpenRouter does report it.
        return BalanceInfo(
            currency=str(first.get("currency", "")),
            total=total_balance,
            used=0.0,
            remaining=total_balance,
            granted=granted,
            source="/user/balance",
        )
