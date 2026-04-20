"""Tests for the OpenAI provider adapter."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from cairn.config._models import ProviderConfig, SecretRef
from cairn.config._secrets import SecretResolver
from cairn.domain._content import (
    ImageBlock,
    ImageSource,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from cairn.domain._enums import StopReason
from cairn.domain._messages import Message
from cairn.domain._provider import (
    MessageStop,
    TextDelta,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    ToolDefinition,
    UsageEvent,
)
from cairn.providers._openai import OpenAIProvider, format_messages, format_tools

from .conftest import make_simple_request

# ---------------------------------------------------------------------------
# Message formatting tests
# ---------------------------------------------------------------------------


class TestFormatMessages:
    def test_system_prepended(self) -> None:
        msg = Message(role="user")
        msg.content = [TextBlock(text="hi")]
        result = format_messages([msg], system="You are helpful")
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are helpful"
        assert result[1]["role"] == "user"

    def test_text_message_string(self) -> None:
        """Single text block should produce a string content, not a list."""
        msg = Message(role="user")
        msg.content = [TextBlock(text="hello")]
        result = format_messages([msg])
        assert result[0]["content"] == "hello"

    def test_tool_use_as_tool_calls(self) -> None:
        msg = Message(role="assistant")
        msg.content = [
            TextBlock(text="Let me check"),
            ToolUseBlock(id="tc-1", name="fetch", input={"url": "x"}),
        ]
        result = format_messages([msg])
        assert "tool_calls" in result[0]
        tc = result[0]["tool_calls"][0]
        assert tc["id"] == "tc-1"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "fetch"

    def test_tool_result_as_tool_role(self) -> None:
        msg = Message(role="user")
        msg.content = [ToolResultBlock(tool_use_id="tc-1", content="result")]
        result = format_messages([msg])
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "tc-1"
        assert result[0]["content"] == "result"

    def test_image_as_image_url(self) -> None:
        msg = Message(role="user")
        msg.content = [ImageBlock(source=ImageSource(media_type="image/png", data="abc"))]
        result = format_messages([msg])
        part = result[0]["content"][0]
        assert part["type"] == "image_url"
        assert part["image_url"]["url"].startswith("data:image/png;base64,")

    def test_thinking_blocks_stripped(self) -> None:
        """OpenAI doesn't support thinking blocks — they should be omitted."""
        msg = Message(role="assistant")
        msg.content = [ThinkingBlock(thinking="hmm"), TextBlock(text="answer")]
        result = format_messages([msg])
        # Should only have the text part, not thinking
        content = result[0]["content"]
        if isinstance(content, str):
            assert content == "answer"
        else:
            assert all(p["type"] != "thinking" for p in content)

    def test_multiple_tool_results(self) -> None:
        """Each tool result should become a separate tool message."""
        msg = Message(role="user")
        msg.content = [
            ToolResultBlock(tool_use_id="tc-1", content="result1"),
            ToolResultBlock(tool_use_id="tc-2", content="result2"),
        ]
        result = format_messages([msg])
        assert len(result) == 2
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "tc-1"
        assert result[1]["role"] == "tool"
        assert result[1]["tool_call_id"] == "tc-2"


class TestFormatTools:
    def test_wraps_in_function(self) -> None:
        tool = ToolDefinition(
            name="fetch",
            description="Fetch URL",
            input_schema={"type": "object"},
        )
        result = format_tools([tool])
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "fetch"
        assert result[0]["function"]["parameters"] == {"type": "object"}


# ---------------------------------------------------------------------------
# Stream chunk mapping tests
# ---------------------------------------------------------------------------


def _make_chunk(
    *,
    content: str | None = None,
    tool_calls: list[Any] | None = None,
    finish_reason: str | None = None,
    usage: Any = None,
) -> SimpleNamespace:
    """Create a fake OpenAI ChatCompletionChunk."""
    delta = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def _make_tool_call_delta(
    *,
    index: int,
    id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
) -> SimpleNamespace:
    """Create a fake tool call delta within a chunk."""
    function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=id, function=function)


class TestChunkMapping:
    def _make_provider(self) -> OpenAIProvider:
        config = ProviderConfig(name="openai", api_key=SecretRef.parse("literal:test"))
        return OpenAIProvider(config, SecretResolver())

    def test_text_delta(self) -> None:
        provider = self._make_provider()
        chunk = _make_chunk(content="Hello")
        results = provider._map_chunk(chunk, {})
        assert len(results) == 1
        assert isinstance(results[0], TextDelta)
        assert results[0].text == "Hello"

    def test_tool_call_start(self) -> None:
        provider = self._make_provider()
        tc = _make_tool_call_delta(index=0, id="call-1", name="fetch", arguments="")
        chunk = _make_chunk(tool_calls=[tc])
        ids: dict[int, str] = {}
        results = provider._map_chunk(chunk, ids)
        starts = [r for r in results if isinstance(r, ToolCallStart)]
        assert len(starts) == 1
        assert starts[0].id == "call-1"
        assert starts[0].name == "fetch"
        assert ids[0] == "call-1"

    def test_tool_call_delta(self) -> None:
        provider = self._make_provider()
        ids = {0: "call-1"}
        tc = _make_tool_call_delta(index=0, arguments='{"url":')
        chunk = _make_chunk(tool_calls=[tc])
        results = provider._map_chunk(chunk, ids)
        deltas = [r for r in results if isinstance(r, ToolCallDelta)]
        assert len(deltas) == 1
        assert deltas[0].id == "call-1"

    def test_tool_call_end_on_finish(self) -> None:
        provider = self._make_provider()
        ids = {0: "call-1", 1: "call-2"}
        chunk = _make_chunk(finish_reason="tool_calls")
        results = provider._map_chunk(chunk, ids)
        ends = [r for r in results if isinstance(r, ToolCallEnd)]
        assert len(ends) == 2
        end_ids = {e.id for e in ends}
        assert end_ids == {"call-1", "call-2"}

    def test_stop_finish(self) -> None:
        provider = self._make_provider()
        chunk = _make_chunk(finish_reason="stop")
        results = provider._map_chunk(chunk, {})
        stops = [r for r in results if isinstance(r, MessageStop)]
        assert len(stops) == 1
        assert stops[0].stop_reason == StopReason.END_TURN

    def test_usage_event(self) -> None:
        provider = self._make_provider()
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=50)
        chunk = SimpleNamespace(choices=[], usage=usage)
        results = provider._map_chunk(chunk, {})
        usages = [r for r in results if isinstance(r, UsageEvent)]
        assert len(usages) == 1
        assert usages[0].input_tokens == 100
        assert usages[0].output_tokens == 50

    def test_empty_chunk(self) -> None:
        provider = self._make_provider()
        chunk = SimpleNamespace(choices=[], usage=None)
        results = provider._map_chunk(chunk, {})
        assert results == []


class TestTokenCounting:
    @pytest.mark.asyncio
    async def test_count_tokens_known_model(self) -> None:
        """tiktoken should work for known OpenAI models."""
        provider = OpenAIProvider(
            ProviderConfig(name="openai", api_key=SecretRef.parse("literal:test")),
            SecretResolver(),
        )
        request = make_simple_request(text="Hello world", model="gpt-4o")
        count = await provider.count_tokens(request)
        assert count > 0

    @pytest.mark.asyncio
    async def test_count_tokens_unknown_model_fallback(self) -> None:
        """Unknown models should fall back to character estimation."""
        provider = OpenAIProvider(
            ProviderConfig(name="openai", api_key=SecretRef.parse("literal:test")),
            SecretResolver(),
        )
        request = make_simple_request(text="Hello world test", model="local-unknown-model")
        count = await provider.count_tokens(request)
        assert count > 0  # chars / 4 estimate
