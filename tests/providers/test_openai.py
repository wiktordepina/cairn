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
from cairn.providers._openai import (
    OpenAIProvider,
    _format_tool_choice,
    _is_reasoning_model,
    _usage_from_chunk,
    format_messages,
    format_tools,
)

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


class TestReasoningModelDetection:
    def test_o1_detected(self) -> None:
        assert _is_reasoning_model("o1")
        assert _is_reasoning_model("o1-preview")
        assert _is_reasoning_model("o1-mini")

    def test_o3_detected(self) -> None:
        assert _is_reasoning_model("o3")
        assert _is_reasoning_model("o3-mini")

    def test_o4_detected(self) -> None:
        assert _is_reasoning_model("o4-mini")

    def test_gpt5_detected(self) -> None:
        assert _is_reasoning_model("gpt-5")
        assert _is_reasoning_model("gpt-5-pro")

    def test_non_reasoning_models(self) -> None:
        assert not _is_reasoning_model("gpt-4o")
        assert not _is_reasoning_model("gpt-4-turbo")
        assert not _is_reasoning_model("local-llama")
        # Don't false-positive on substrings:
        assert not _is_reasoning_model("o1custom")  # no hyphen
        assert not _is_reasoning_model("not-o1")


class TestUsageReasoningTokens:
    def test_reasoning_tokens_surfaced_when_present(self) -> None:
        usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=200,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=150),
        )
        ev = _usage_from_chunk(usage)
        assert ev.output_tokens == 200
        assert ev.reasoning_tokens == 150

    def test_reasoning_tokens_default_zero_when_absent(self) -> None:
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=50)
        ev = _usage_from_chunk(usage)
        assert ev.reasoning_tokens == 0

    def test_reasoning_tokens_none_coerced_zero(self) -> None:
        usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=50,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=None),
        )
        ev = _usage_from_chunk(usage)
        assert ev.reasoning_tokens == 0


class TestToolChoiceTranslation:
    def test_returns_none_when_unset(self) -> None:
        assert _format_tool_choice(None, provider_name="openai") is None

    def test_auto(self) -> None:
        assert _format_tool_choice("auto", provider_name="openai") == "auto"

    def test_none(self) -> None:
        assert _format_tool_choice("none", provider_name="openai") == "none"

    def test_any_translates_to_required(self) -> None:
        assert _format_tool_choice("any", provider_name="openai") == "required"

    def test_named_tool_object_form(self) -> None:
        assert _format_tool_choice(("tool", "web_fetch"), provider_name="openai") == {
            "type": "function",
            "function": {"name": "web_fetch"},
        }

    def test_unrecognised_string_warns_and_returns_none(self, caplog: Any) -> None:
        with caplog.at_level("WARNING", logger="cairn.providers._openai"):
            result = _format_tool_choice("ultra", provider_name="openai")
        assert result is None
        assert any("unrecognised tool_choice" in r.message for r in caplog.records)


class TestImageDetail:
    def test_default_auto_omits_field_in_payload(self) -> None:
        msg = Message(role="user")
        msg.content = [ImageBlock(source=ImageSource(media_type="image/png", data="abc"))]
        block = format_messages([msg])[0]["content"][0]
        assert block["type"] == "image_url"
        assert "detail" not in block["image_url"]

    def test_explicit_high_forwarded(self) -> None:
        msg = Message(role="user")
        msg.content = [
            ImageBlock(source=ImageSource(media_type="image/png", data="abc", detail="high"))
        ]
        block = format_messages([msg])[0]["content"][0]
        assert block["image_url"]["detail"] == "high"

    def test_explicit_low_forwarded(self) -> None:
        msg = Message(role="user")
        msg.content = [
            ImageBlock(source=ImageSource(media_type="image/png", data="abc", detail="low"))
        ]
        block = format_messages([msg])[0]["content"][0]
        assert block["image_url"]["detail"] == "low"


class TestKwargsBuilding:
    def _make_provider(self) -> OpenAIProvider:
        config = ProviderConfig(name="openai", api_key=SecretRef.parse("literal:test"))
        return OpenAIProvider(config, SecretResolver())

    def test_non_reasoning_uses_max_tokens(self) -> None:
        provider = self._make_provider()
        req = make_simple_request(text="hi", model="gpt-4o")
        kwargs = provider._build_kwargs(req)
        assert "max_tokens" in kwargs
        assert "max_completion_tokens" not in kwargs

    def test_reasoning_uses_max_completion_tokens(self) -> None:
        provider = self._make_provider()
        req = make_simple_request(text="hi", model="o3-mini")
        kwargs = provider._build_kwargs(req)
        assert "max_completion_tokens" in kwargs
        assert "max_tokens" not in kwargs

    def test_reasoning_drops_temperature(self) -> None:
        provider = self._make_provider()
        from cairn.domain._messages import Message
        from cairn.domain._provider import ProviderRequest

        msg = Message(role="user")
        msg.content = [TextBlock(text="hi")]
        req = ProviderRequest(model="o3-mini", messages=[msg], temperature=0.7)
        kwargs = provider._build_kwargs(req)
        assert "temperature" not in kwargs

    def test_non_reasoning_keeps_temperature(self) -> None:
        provider = self._make_provider()
        from cairn.domain._messages import Message
        from cairn.domain._provider import ProviderRequest

        msg = Message(role="user")
        msg.content = [TextBlock(text="hi")]
        req = ProviderRequest(model="gpt-4o", messages=[msg], temperature=0.7)
        kwargs = provider._build_kwargs(req)
        assert kwargs["temperature"] == 0.7

    def test_reasoning_effort_only_on_reasoning_model(self) -> None:
        provider = self._make_provider()
        from cairn.domain._messages import Message
        from cairn.domain._provider import ProviderRequest

        msg = Message(role="user")
        msg.content = [TextBlock(text="hi")]
        # Non-reasoning: dropped silently
        req_chat = ProviderRequest(model="gpt-4o", messages=[msg], reasoning_effort="high")
        assert "reasoning_effort" not in provider._build_kwargs(req_chat)
        # Reasoning: forwarded verbatim
        req_o3 = ProviderRequest(model="o3-mini", messages=[msg], reasoning_effort="high")
        assert provider._build_kwargs(req_o3)["reasoning_effort"] == "high"

    def test_prompt_cache_key_forwarded(self) -> None:
        provider = self._make_provider()
        from cairn.domain._messages import Message
        from cairn.domain._provider import ProviderRequest

        msg = Message(role="user")
        msg.content = [TextBlock(text="hi")]
        req = ProviderRequest(model="gpt-4o", messages=[msg], prompt_cache_key="cairn:session-abc")
        kwargs = provider._build_kwargs(req)
        assert kwargs["prompt_cache_key"] == "cairn:session-abc"

    def test_tool_choice_forwarded(self) -> None:
        provider = self._make_provider()
        from cairn.domain._messages import Message
        from cairn.domain._provider import ProviderRequest

        msg = Message(role="user")
        msg.content = [TextBlock(text="hi")]
        tool = ToolDefinition(name="fetch", description="x", input_schema={})
        req = ProviderRequest(model="gpt-4o", messages=[msg], tools=[tool], tool_choice="any")
        # "any" is normalised to OpenAI's "required"
        assert provider._build_kwargs(req)["tool_choice"] == "required"

    def test_parallel_tool_calls_disabled_when_requested(self) -> None:
        provider = self._make_provider()
        from cairn.domain._messages import Message
        from cairn.domain._provider import ProviderRequest

        msg = Message(role="user")
        msg.content = [TextBlock(text="hi")]
        tool = ToolDefinition(name="fetch", description="x", input_schema={})
        req = ProviderRequest(
            model="gpt-4o",
            messages=[msg],
            tools=[tool],
            disable_parallel_tool_use=True,
        )
        assert provider._build_kwargs(req)["parallel_tool_calls"] is False

    def test_parallel_tool_calls_omitted_when_no_tools(self) -> None:
        """The flag has no meaning without tools — don't send it."""
        provider = self._make_provider()
        from cairn.domain._messages import Message
        from cairn.domain._provider import ProviderRequest

        msg = Message(role="user")
        msg.content = [TextBlock(text="hi")]
        req = ProviderRequest(model="gpt-4o", messages=[msg], disable_parallel_tool_use=True)
        assert "parallel_tool_calls" not in provider._build_kwargs(req)


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
