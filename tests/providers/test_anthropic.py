"""Tests for the Anthropic provider adapter."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from cairn.config._models import ProviderConfig, SecretRef
from cairn.config._secrets import SecretResolver
from cairn.domain._content import (
    ImageBlock,
    ImageSource,
    RedactedThinkingBlock,
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
    ThinkingDelta,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    ToolDefinition,
    UsageEvent,
)
from cairn.providers._anthropic import (
    AnthropicProvider,
    _format_tool_choice,
    _resolve_thinking_budget,
    format_messages,
    format_tools,
)

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

    def test_thinking_message_omits_signature_when_empty(self) -> None:
        msg = Message(role="assistant")
        msg.content = [ThinkingBlock(thinking="hmm")]
        block = format_messages([msg])[0]["content"][0]
        assert "signature" not in block

    def test_thinking_message_includes_signature_when_set(self) -> None:
        msg = Message(role="assistant")
        msg.content = [ThinkingBlock(thinking="hmm", signature="sig-abc")]
        block = format_messages([msg])[0]["content"][0]
        assert block["signature"] == "sig-abc"

    def test_redacted_thinking_message(self) -> None:
        msg = Message(role="assistant")
        msg.content = [RedactedThinkingBlock(data="opaque")]
        block = format_messages([msg])[0]["content"][0]
        assert block == {"type": "redacted_thinking", "data": "opaque"}


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


def _new_state() -> tuple[dict[int, str], set[int], dict[str, int]]:
    """Fresh per-test mapper state (block_ids, thinking_indices, usage_acc)."""
    return (
        {},
        set(),
        {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        },
    )


class TestStreamMapping:
    def _make_provider(self) -> AnthropicProvider:
        config = ProviderConfig(name="anthropic", api_key=SecretRef.parse("literal:test"))
        return AnthropicProvider(config, SecretResolver())

    def test_text_delta(self) -> None:
        provider = self._make_provider()
        block_ids, thinking_idx, usage = _new_state()
        event = _make_sdk_event(
            "content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="Hello"),
        )
        results = provider._map_event(event, block_ids, thinking_idx, usage)
        assert len(results) == 1
        assert isinstance(results[0], TextDelta)
        assert results[0].text == "Hello"

    def test_tool_call_start(self) -> None:
        provider = self._make_provider()
        block_ids, thinking_idx, usage = _new_state()
        event = _make_sdk_event(
            "content_block_start",
            index=1,
            content_block=SimpleNamespace(type="tool_use", id="tc-1", name="fetch"),
        )
        results = provider._map_event(event, block_ids, thinking_idx, usage)
        assert len(results) == 1
        assert isinstance(results[0], ToolCallStart)
        assert results[0].id == "tc-1"
        assert results[0].name == "fetch"
        assert block_ids[1] == "tc-1"

    def test_tool_call_delta(self) -> None:
        provider = self._make_provider()
        block_ids, thinking_idx, usage = _new_state()
        block_ids[1] = "tc-1"
        event = _make_sdk_event(
            "content_block_delta",
            index=1,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"url":'),
        )
        results = provider._map_event(event, block_ids, thinking_idx, usage)
        assert len(results) == 1
        assert isinstance(results[0], ToolCallDelta)
        assert results[0].id == "tc-1"

    def test_tool_call_end(self) -> None:
        provider = self._make_provider()
        block_ids, thinking_idx, usage = _new_state()
        block_ids[1] = "tc-1"
        event = _make_sdk_event("content_block_stop", index=1)
        results = provider._map_event(event, block_ids, thinking_idx, usage)
        assert len(results) == 1
        assert isinstance(results[0], ToolCallEnd)
        assert results[0].id == "tc-1"
        assert 1 not in block_ids  # cleaned up

    def test_message_start_buffers_usage_no_event(self) -> None:
        """``message_start`` no longer emits a UsageEvent — the values are
        accumulated and merged into the single emit at ``message_stop``."""
        provider = self._make_provider()
        block_ids, thinking_idx, usage = _new_state()
        event = _make_sdk_event(
            "message_start",
            message=SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=100,
                    output_tokens=3,
                    cache_read_input_tokens=20,
                    cache_creation_input_tokens=15,
                )
            ),
        )
        results = provider._map_event(event, block_ids, thinking_idx, usage)
        assert results == []
        assert usage["input_tokens"] == 100
        assert usage["output_tokens"] == 3
        assert usage["cache_read_tokens"] == 20
        assert usage["cache_write_tokens"] == 15

    def test_message_delta_emits_single_aggregated_usage(self) -> None:
        """End-of-stream emits ONE UsageEvent merging message_start and
        the cumulative message_delta total — fixes the prior double-count."""
        provider = self._make_provider()
        block_ids, thinking_idx, usage = _new_state()
        # message_start arrived first
        provider._map_event(
            _make_sdk_event(
                "message_start",
                message=SimpleNamespace(
                    usage=SimpleNamespace(
                        input_tokens=100,
                        output_tokens=3,
                        cache_read_input_tokens=20,
                        cache_creation_input_tokens=15,
                    )
                ),
            ),
            block_ids,
            thinking_idx,
            usage,
        )
        # message_delta carries the cumulative final output_tokens.
        event = _make_sdk_event(
            "message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=50),
        )
        results = provider._map_event(event, block_ids, thinking_idx, usage)
        assert len(results) == 2
        assert isinstance(results[0], UsageEvent)
        # input + cache survive from message_start; output is the FINAL
        # cumulative count (not 50 + 3 — the prior code's bug).
        assert results[0].input_tokens == 100
        assert results[0].output_tokens == 50
        assert results[0].cache_read_tokens == 20
        assert results[0].cache_write_tokens == 15
        assert isinstance(results[1], MessageStop)
        assert results[1].stop_reason == StopReason.END_TURN

    def test_text_block_start_ignored(self) -> None:
        """Text block starts don't produce events — deltas do."""
        provider = self._make_provider()
        block_ids, thinking_idx, usage = _new_state()
        event = _make_sdk_event(
            "content_block_start",
            index=0,
            content_block=SimpleNamespace(type="text", text=""),
        )
        results = provider._map_event(event, block_ids, thinking_idx, usage)
        assert results == []


class TestThinkingStreamMapping:
    def _make_provider(self) -> AnthropicProvider:
        config = ProviderConfig(name="anthropic", api_key=SecretRef.parse("literal:test"))
        return AnthropicProvider(config, SecretResolver())

    def test_thinking_block_start_tracks_index(self) -> None:
        provider = self._make_provider()
        block_ids, thinking_idx, usage = _new_state()
        event = _make_sdk_event(
            "content_block_start",
            index=0,
            content_block=SimpleNamespace(type="thinking", thinking="", signature=""),
        )
        provider._map_event(event, block_ids, thinking_idx, usage)
        assert 0 in thinking_idx

    def test_thinking_delta_emits_thinking_event(self) -> None:
        provider = self._make_provider()
        block_ids, thinking_idx, usage = _new_state()
        thinking_idx.add(0)
        event = _make_sdk_event(
            "content_block_delta",
            index=0,
            delta=SimpleNamespace(type="thinking_delta", thinking="Let me reason"),
        )
        results = provider._map_event(event, block_ids, thinking_idx, usage)
        assert len(results) == 1
        assert isinstance(results[0], ThinkingDelta)
        assert results[0].text == "Let me reason"
        assert results[0].signature == ""

    def test_signature_delta_emits_signature_only(self) -> None:
        provider = self._make_provider()
        block_ids, thinking_idx, usage = _new_state()
        thinking_idx.add(0)
        event = _make_sdk_event(
            "content_block_delta",
            index=0,
            delta=SimpleNamespace(type="signature_delta", signature="sig-xyz"),
        )
        results = provider._map_event(event, block_ids, thinking_idx, usage)
        assert len(results) == 1
        assert isinstance(results[0], ThinkingDelta)
        assert results[0].text == ""
        assert results[0].signature == "sig-xyz"

    def test_thinking_block_stop_clears_index_no_tool_event(self) -> None:
        """Closing a thinking block must NOT emit ToolCallEnd."""
        provider = self._make_provider()
        block_ids, thinking_idx, usage = _new_state()
        thinking_idx.add(0)
        event = _make_sdk_event("content_block_stop", index=0)
        results = provider._map_event(event, block_ids, thinking_idx, usage)
        assert results == []
        assert 0 not in thinking_idx

    def test_redacted_thinking_logs_and_skips(self, caplog: Any) -> None:
        provider = self._make_provider()
        block_ids, thinking_idx, usage = _new_state()
        event = _make_sdk_event(
            "content_block_start",
            index=0,
            content_block=SimpleNamespace(type="redacted_thinking", data="opaque"),
        )
        with caplog.at_level("WARNING", logger="cairn.providers._anthropic"):
            results = provider._map_event(event, block_ids, thinking_idx, usage)
        assert results == []
        assert any("redacted_thinking" in rec.message for rec in caplog.records)


class TestToolChoiceTranslation:
    def test_returns_none_when_unset(self) -> None:
        assert (
            _format_tool_choice(
                None, disable_parallel=False, thinking_on=False, provider_name="anthropic"
            )
            is None
        )

    def test_auto(self) -> None:
        assert _format_tool_choice(
            "auto", disable_parallel=False, thinking_on=False, provider_name="anthropic"
        ) == {"type": "auto"}

    def test_any(self) -> None:
        assert _format_tool_choice(
            "any", disable_parallel=False, thinking_on=False, provider_name="anthropic"
        ) == {"type": "any"}

    def test_none(self) -> None:
        assert _format_tool_choice(
            "none", disable_parallel=False, thinking_on=False, provider_name="anthropic"
        ) == {"type": "none"}

    def test_named_tool(self) -> None:
        assert _format_tool_choice(
            ("tool", "web_fetch"),
            disable_parallel=False,
            thinking_on=False,
            provider_name="anthropic",
        ) == {"type": "tool", "name": "web_fetch"}

    def test_disable_parallel_alone_implies_auto_wrapper(self) -> None:
        assert _format_tool_choice(
            None,
            disable_parallel=True,
            thinking_on=False,
            provider_name="anthropic",
        ) == {"type": "auto", "disable_parallel_tool_use": True}

    def test_disable_parallel_with_any(self) -> None:
        result = _format_tool_choice(
            "any",
            disable_parallel=True,
            thinking_on=False,
            provider_name="anthropic",
        )
        assert result == {"type": "any", "disable_parallel_tool_use": True}

    def test_thinking_on_downgrades_any_to_auto(self, caplog: Any) -> None:
        with caplog.at_level("WARNING", logger="cairn.providers._anthropic"):
            result = _format_tool_choice(
                "any", disable_parallel=False, thinking_on=True, provider_name="anthropic"
            )
        assert result == {"type": "auto"}
        assert any("incompatible with extended thinking" in r.message for r in caplog.records)

    def test_thinking_on_downgrades_named_tool(self) -> None:
        result = _format_tool_choice(
            ("tool", "web_fetch"),
            disable_parallel=False,
            thinking_on=True,
            provider_name="anthropic",
        )
        assert result == {"type": "auto"}

    def test_thinking_on_keeps_none(self) -> None:
        result = _format_tool_choice(
            "none", disable_parallel=False, thinking_on=True, provider_name="anthropic"
        )
        assert result == {"type": "none"}


class TestReasoningEffortBudget:
    def test_none_returns_none(self) -> None:
        assert _resolve_thinking_budget(None, max_tokens=8000) is None

    def test_minimal_returns_none(self) -> None:
        assert _resolve_thinking_budget("minimal", max_tokens=8000) is None

    def test_low_is_25_percent(self) -> None:
        assert _resolve_thinking_budget("low", max_tokens=8000) == 2000

    def test_medium_is_50_percent(self) -> None:
        assert _resolve_thinking_budget("medium", max_tokens=8000) == 4000

    def test_high_is_75_percent(self) -> None:
        assert _resolve_thinking_budget("high", max_tokens=8000) == 6000

    def test_xhigh_is_90_percent(self) -> None:
        assert _resolve_thinking_budget("xhigh", max_tokens=8000) == 7200

    def test_floor_at_minimum_when_max_tokens_small(self) -> None:
        # 25% of 4096 = 1024, exactly the floor
        assert _resolve_thinking_budget("low", max_tokens=4096) == 1024
        # 25% of 2000 = 500 → floored to 1024 (still < max_tokens)
        assert _resolve_thinking_budget("low", max_tokens=2000) == 1024

    def test_returns_none_when_max_tokens_below_floor(self) -> None:
        # max_tokens=1000 can't fit a 1024-token thinking budget
        assert _resolve_thinking_budget("high", max_tokens=1000) is None

    def test_unknown_effort_warns_and_returns_none(self, caplog: Any) -> None:
        with caplog.at_level("WARNING", logger="cairn.providers._anthropic"):
            result = _resolve_thinking_budget("ultra", max_tokens=8000)
        assert result is None
        assert any("unknown reasoning_effort" in r.message for r in caplog.records)
