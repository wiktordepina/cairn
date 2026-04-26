"""Tests for Anthropic prompt-caching translation + usage parsing."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from cairn.config._models import ProviderConfig, SecretRef
from cairn.config._secrets import SecretResolver
from cairn.domain._content import TextBlock
from cairn.domain._messages import Message
from cairn.domain._provider import (
    SystemPromptSegment,
    ToolDefinition,
    UsageEvent,
)
from cairn.providers._anthropic import (
    AnthropicProvider,
    _enforce_marker_cap,
    format_messages,
    format_system,
    format_tools,
)


class TestFormatSystem:
    def test_string_passes_through(self) -> None:
        assert format_system("identity") == "identity"

    def test_none_passes_through(self) -> None:
        assert format_system(None) is None

    def test_segment_list_with_cacheable(self) -> None:
        segs = [
            SystemPromptSegment(text="profile", cacheable=True),
            SystemPromptSegment(text="session", cacheable=True),
        ]
        result = format_system(segs)
        assert result == [
            {
                "type": "text",
                "text": "profile",
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": "session",
                "cache_control": {"type": "ephemeral"},
            },
        ]

    def test_segment_list_without_cacheable(self) -> None:
        segs = [SystemPromptSegment(text="x", cacheable=False)]
        assert format_system(segs) == [{"type": "text", "text": "x"}]


class TestFormatToolsCacheLast:
    def test_no_marker_when_cache_last_false(self) -> None:
        tools = [ToolDefinition(name="t", description="t", input_schema={})]
        result = format_tools(tools, cache_last=False)
        assert "cache_control" not in result[0]

    def test_marker_only_on_last_tool(self) -> None:
        tools = [
            ToolDefinition(name="a", description="a", input_schema={}),
            ToolDefinition(name="b", description="b", input_schema={}),
            ToolDefinition(name="c", description="c", input_schema={}),
        ]
        result = format_tools(tools, cache_last=True)
        assert "cache_control" not in result[0]
        assert "cache_control" not in result[1]
        assert result[2]["cache_control"] == {"type": "ephemeral"}

    def test_empty_tools_no_marker_no_error(self) -> None:
        assert format_tools([], cache_last=True) == []


class TestFormatMessagesCacheLast:
    def test_no_marker_when_cache_last_false(self) -> None:
        msg = Message(role="user")
        msg.content = [TextBlock(text="hi")]
        result = format_messages([msg], cache_last=False)
        assert "cache_control" not in result[0]["content"][0]

    def test_marker_on_last_block_of_last_message(self) -> None:
        m1 = Message(role="user")
        m1.content = [TextBlock(text="first")]
        m2 = Message(role="assistant")
        m2.content = [TextBlock(text="reply"), TextBlock(text="more")]
        result = format_messages([m1, m2], cache_last=True)
        assert "cache_control" not in result[0]["content"][0]
        assert "cache_control" not in result[1]["content"][0]
        assert result[1]["content"][1]["cache_control"] == {"type": "ephemeral"}

    def test_empty_messages_no_marker_no_error(self) -> None:
        assert format_messages([], cache_last=True) == []


def _drive_to_stop(
    provider: AnthropicProvider,
    start_usage: SimpleNamespace,
    *,
    final_output_tokens: int = 50,
) -> UsageEvent:
    """Run a minimal message_start → message_delta(end_turn) sequence and
    return the single aggregated ``UsageEvent`` that lands at end-of-stream.
    """
    block_ids: dict[int, str] = {}
    thinking_idx: set[int] = set()
    usage_acc = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    provider._map_event(
        SimpleNamespace(type="message_start", message=SimpleNamespace(usage=start_usage)),
        block_ids,
        thinking_idx,
        usage_acc,
    )
    results = provider._map_event(
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=final_output_tokens),
        ),
        block_ids,
        thinking_idx,
        usage_acc,
    )
    usage_events = [r for r in results if isinstance(r, UsageEvent)]
    assert len(usage_events) == 1, "expected exactly one UsageEvent at message_stop"
    return usage_events[0]


class TestUsageWithCache:
    def _make_provider(self) -> AnthropicProvider:
        config = ProviderConfig(name="anthropic", api_key=SecretRef.parse("literal:t"))
        return AnthropicProvider(config, SecretResolver())

    def test_message_start_populates_cache_fields(self) -> None:
        """Cache fields buffered at message_start surface in the aggregated
        end-of-stream UsageEvent."""
        provider = self._make_provider()
        ev = _drive_to_stop(
            provider,
            SimpleNamespace(
                input_tokens=100,
                output_tokens=0,
                cache_creation_input_tokens=200,
                cache_read_input_tokens=300,
            ),
        )
        assert ev.input_tokens == 100
        assert ev.cache_write_tokens == 200
        assert ev.cache_read_tokens == 300

    def test_message_start_missing_cache_fields_defaults_zero(self) -> None:
        """Older SDK versions / fixtures may not include cache fields."""
        provider = self._make_provider()
        ev = _drive_to_stop(
            provider,
            SimpleNamespace(input_tokens=42, output_tokens=0),
        )
        assert ev.cache_read_tokens == 0
        assert ev.cache_write_tokens == 0

    def test_message_start_none_cache_fields_coerced_zero(self) -> None:
        """SDK sometimes sets the attribute to None rather than omitting."""
        provider = self._make_provider()
        ev = _drive_to_stop(
            provider,
            SimpleNamespace(
                input_tokens=42,
                output_tokens=0,
                cache_creation_input_tokens=None,
                cache_read_input_tokens=None,
            ),
        )
        assert ev.cache_read_tokens == 0
        assert ev.cache_write_tokens == 0


class TestMarkerCapEnforcement:
    def test_under_cap_unchanged(self) -> None:
        system = [
            {"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}},
        ]
        tools = [
            {
                "name": "t",
                "description": "t",
                "input_schema": {},
                "cache_control": {"type": "ephemeral"},
            },
        ]
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "x", "cache_control": {"type": "ephemeral"}},
                ],
            },
        ]
        _enforce_marker_cap(
            system=system, tools=tools, messages=messages, provider_name="anthropic"
        )
        # All three markers preserved.
        assert "cache_control" in system[0]
        assert "cache_control" in tools[0]
        assert "cache_control" in messages[0]["content"][0]

    def test_over_cap_drops_oldest_first_warns(self, caplog: logging.LogCaptureFixture) -> None:
        system = [
            {"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "b", "cache_control": {"type": "ephemeral"}},
        ]
        tools = [
            {
                "name": "t",
                "description": "t",
                "input_schema": {},
                "cache_control": {"type": "ephemeral"},
            },
        ]
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "x", "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": "y", "cache_control": {"type": "ephemeral"}},
                ],
            },
        ]
        with caplog.at_level(logging.WARNING, logger="cairn.providers._anthropic"):
            _enforce_marker_cap(
                system=system,
                tools=tools,
                messages=messages,
                provider_name="anthropic",
            )

        # 5 markers, cap 4 → 1 dropped, oldest (system[0]) goes first.
        assert "cache_control" not in system[0]
        assert "cache_control" in system[1]
        assert "cache_control" in tools[0]
        assert "cache_control" in messages[0]["content"][0]
        assert "cache_control" in messages[0]["content"][1]
        assert any("cache markers" in r.message for r in caplog.records)
