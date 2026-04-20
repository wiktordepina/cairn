"""Tests for the Anthropic provider adapter."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

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
from cairn.providers._anthropic import AnthropicProvider, format_messages, format_tools

# ---------------------------------------------------------------------------
# Message formatting tests
# ---------------------------------------------------------------------------


class TestFormatMessages:
    def test_text_message(self) -> None:
        msg = Message(role="user")
        msg.content = [TextBlock(text="hello")]
        result = format_messages([msg])
        assert result == [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]

    def test_tool_use_message(self) -> None:
        msg = Message(role="assistant")
        msg.content = [ToolUseBlock(id="tc-1", name="fetch", input={"url": "x"})]
        result = format_messages([msg])
        assert result[0]["content"][0]["type"] == "tool_use"
        assert result[0]["content"][0]["id"] == "tc-1"
        assert result[0]["content"][0]["name"] == "fetch"
        assert result[0]["content"][0]["input"] == {"url": "x"}

    def test_tool_result_message(self) -> None:
        msg = Message(role="user")
        msg.content = [ToolResultBlock(tool_use_id="tc-1", content="done")]
        result = format_messages([msg])
        assert result[0]["role"] == "user"
        assert result[0]["content"][0]["type"] == "tool_result"
        assert result[0]["content"][0]["tool_use_id"] == "tc-1"

    def test_image_message(self) -> None:
        msg = Message(role="user")
        msg.content = [ImageBlock(source=ImageSource(media_type="image/png", data="abc"))]
        result = format_messages([msg])
        block = result[0]["content"][0]
        assert block["type"] == "image"
        assert block["source"]["type"] == "base64"
        assert block["source"]["media_type"] == "image/png"

    def test_thinking_message(self) -> None:
        msg = Message(role="assistant")
        msg.content = [ThinkingBlock(thinking="let me think")]
        result = format_messages([msg])
        assert result[0]["content"][0]["type"] == "thinking"


class TestFormatTools:
    def test_basic_tool(self) -> None:
        tool = ToolDefinition(
            name="fetch",
            description="Fetch URL",
            input_schema={"type": "object", "properties": {"url": {"type": "string"}}},
        )
        result = format_tools([tool])
        assert result[0]["name"] == "fetch"
        assert result[0]["description"] == "Fetch URL"
        assert "input_schema" in result[0]


# ---------------------------------------------------------------------------
# Stream event mapping tests
# ---------------------------------------------------------------------------


def _make_sdk_event(type: str, **kwargs: Any) -> SimpleNamespace:
    """Create a fake Anthropic SDK event."""
    return SimpleNamespace(type=type, **kwargs)


class TestStreamMapping:
    def _make_provider(self) -> AnthropicProvider:
        config = ProviderConfig(name="anthropic", api_key=SecretRef.parse("literal:test"))
        return AnthropicProvider(config, SecretResolver())

    def test_text_delta(self) -> None:
        provider = self._make_provider()
        event = _make_sdk_event(
            "content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="Hello"),
        )
        results = provider._map_event(event, {})
        assert len(results) == 1
        assert isinstance(results[0], TextDelta)
        assert results[0].text == "Hello"

    def test_tool_call_start(self) -> None:
        provider = self._make_provider()
        event = _make_sdk_event(
            "content_block_start",
            index=1,
            content_block=SimpleNamespace(type="tool_use", id="tc-1", name="fetch"),
        )
        block_ids: dict[int, str] = {}
        results = provider._map_event(event, block_ids)
        assert len(results) == 1
        assert isinstance(results[0], ToolCallStart)
        assert results[0].id == "tc-1"
        assert results[0].name == "fetch"
        assert block_ids[1] == "tc-1"

    def test_tool_call_delta(self) -> None:
        provider = self._make_provider()
        block_ids = {1: "tc-1"}
        event = _make_sdk_event(
            "content_block_delta",
            index=1,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"url":'),
        )
        results = provider._map_event(event, block_ids)
        assert len(results) == 1
        assert isinstance(results[0], ToolCallDelta)
        assert results[0].id == "tc-1"

    def test_tool_call_end(self) -> None:
        provider = self._make_provider()
        block_ids = {1: "tc-1"}
        event = _make_sdk_event("content_block_stop", index=1)
        results = provider._map_event(event, block_ids)
        assert len(results) == 1
        assert isinstance(results[0], ToolCallEnd)
        assert results[0].id == "tc-1"
        assert 1 not in block_ids  # cleaned up

    def test_message_start_usage(self) -> None:
        provider = self._make_provider()
        event = _make_sdk_event(
            "message_start",
            message=SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=0)),
        )
        results = provider._map_event(event, {})
        assert len(results) == 1
        assert isinstance(results[0], UsageEvent)
        assert results[0].input_tokens == 100

    def test_message_delta_with_stop(self) -> None:
        provider = self._make_provider()
        event = _make_sdk_event(
            "message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=50),
        )
        results = provider._map_event(event, {})
        assert len(results) == 2
        assert isinstance(results[0], UsageEvent)
        assert results[0].output_tokens == 50
        assert isinstance(results[1], MessageStop)
        assert results[1].stop_reason == StopReason.END_TURN

    def test_text_block_start_ignored(self) -> None:
        """Text block starts don't produce events — deltas do."""
        provider = self._make_provider()
        event = _make_sdk_event(
            "content_block_start",
            index=0,
            content_block=SimpleNamespace(type="text", text=""),
        )
        results = provider._map_event(event, {})
        assert results == []
