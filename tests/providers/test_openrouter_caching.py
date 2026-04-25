"""Tests for OpenRouter prompt-caching: marker emission + dual usage shape."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from cairn.config._models import ProviderConfig, SecretRef
from cairn.config._secrets import SecretResolver
from cairn.domain._content import TextBlock
from cairn.domain._messages import Message
from cairn.domain._provider import (
    ProviderRequest,
    SystemPromptSegment,
    ToolDefinition,
    UsageEvent,
)
from cairn.providers._openrouter import (
    OpenRouterProvider,
    _enforce_marker_cap,
    _format_messages_with_cache,
    _format_system_with_cache,
    _format_tools_with_cache,
    _request_uses_cache,
    _usage_from_chunk,
)


class TestRequestUsesCache:
    def test_no_cache_flags_or_segments(self) -> None:
        req = ProviderRequest(model="x", messages=[])
        assert _request_uses_cache(req) is False

    def test_cache_tools_flag(self) -> None:
        req = ProviderRequest(model="x", messages=[], cache_tools=True)
        assert _request_uses_cache(req) is True

    def test_cache_last_message_flag(self) -> None:
        req = ProviderRequest(model="x", messages=[], cache_last_message=True)
        assert _request_uses_cache(req) is True

    def test_cacheable_segment(self) -> None:
        req = ProviderRequest(
            model="x",
            messages=[],
            system=[SystemPromptSegment(text="x", cacheable=True)],
        )
        assert _request_uses_cache(req) is True

    def test_non_cacheable_segments_only(self) -> None:
        req = ProviderRequest(
            model="x",
            messages=[],
            system=[SystemPromptSegment(text="x", cacheable=False)],
        )
        assert _request_uses_cache(req) is False


class TestFormatSystemWithCache:
    def test_string_yields_role_system(self) -> None:
        msg = _format_system_with_cache("hello")
        assert msg == {
            "role": "system",
            "content": [{"type": "text", "text": "hello"}],
        }

    def test_segments_with_cache_control(self) -> None:
        segs = [
            SystemPromptSegment(text="profile", cacheable=True),
            SystemPromptSegment(text="session", cacheable=True),
        ]
        msg = _format_system_with_cache(segs)
        assert msg is not None
        assert msg["role"] == "system"
        assert msg["content"] == [
            {"type": "text", "text": "profile", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "session", "cache_control": {"type": "ephemeral"}},
        ]

    def test_none_returns_none(self) -> None:
        assert _format_system_with_cache(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _format_system_with_cache("") is None

    def test_empty_segments_returns_none(self) -> None:
        assert _format_system_with_cache([]) is None
        assert _format_system_with_cache([SystemPromptSegment(text="", cacheable=True)]) is None


class TestFormatToolsWithCache:
    def test_marker_only_on_last_tool(self) -> None:
        tools = [
            ToolDefinition(name="a", description="a", input_schema={}),
            ToolDefinition(name="b", description="b", input_schema={}),
        ]
        result = _format_tools_with_cache(tools, cache_last=True)
        assert "cache_control" not in result[0]
        assert result[1]["cache_control"] == {"type": "ephemeral"}

    def test_no_marker_when_cache_last_false(self) -> None:
        tools = [ToolDefinition(name="t", description="t", input_schema={})]
        result = _format_tools_with_cache(tools, cache_last=False)
        assert "cache_control" not in result[0]


class TestFormatMessagesWithCache:
    def test_marker_on_last_text_part_of_last_message(self) -> None:
        m1 = Message(role="user")
        m1.content = [TextBlock(text="first")]
        m2 = Message(role="assistant")
        m2.content = [TextBlock(text="reply"), TextBlock(text="more")]
        result = _format_messages_with_cache([m1, m2], cache_last=True)
        # First message untouched
        assert "cache_control" not in result[0]["content"][0]
        # Last message: cache_control on the LAST text part only
        assert "cache_control" not in result[1]["content"][0]
        assert result[1]["content"][1]["cache_control"] == {"type": "ephemeral"}

    def test_content_kept_as_list_for_marker_anchoring(self) -> None:
        """Single-text-part messages still arrive as content lists so
        cache_control can attach — this is the OpenAI shape, valid for
        OpenRouter."""
        m = Message(role="user")
        m.content = [TextBlock(text="hi")]
        result = _format_messages_with_cache([m], cache_last=False)
        assert isinstance(result[0]["content"], list)


class TestUsageDualShape:
    def test_anthropic_shape_wins(self) -> None:
        usage = SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=10,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=300,
            prompt_tokens_details=SimpleNamespace(cached_tokens=500),
        )
        ev = _usage_from_chunk(usage)
        assert isinstance(ev, UsageEvent)
        # Anthropic-shaped fields take precedence
        assert ev.cache_read_tokens == 300
        assert ev.cache_write_tokens == 200

    def test_openai_shape_used_when_anthropic_absent(self) -> None:
        usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=5,
            prompt_tokens_details=SimpleNamespace(cached_tokens=42),
        )
        ev = _usage_from_chunk(usage)
        assert ev.cache_read_tokens == 42
        assert ev.cache_write_tokens == 0

    def test_no_cache_fields(self) -> None:
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        ev = _usage_from_chunk(usage)
        assert ev.cache_read_tokens == 0
        assert ev.cache_write_tokens == 0


class TestMarkerCap:
    def test_drops_oldest_first_warns(self, caplog: logging.LogCaptureFixture) -> None:
        system_msg = {
            "role": "system",
            "content": [
                {"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "b", "cache_control": {"type": "ephemeral"}},
            ],
        }
        tools = [
            {"type": "function", "function": {}, "cache_control": {"type": "ephemeral"}},
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
        with caplog.at_level(logging.WARNING, logger="cairn.providers._openrouter"):
            _enforce_marker_cap(
                system_msg=system_msg,
                tools=tools,
                messages=messages,
                provider_name="openrouter",
            )
        # 5 markers, cap 4 → drop one (system content[0] goes first).
        assert "cache_control" not in system_msg["content"][0]
        assert "cache_control" in system_msg["content"][1]
        assert any("cache markers" in r.message for r in caplog.records)


class TestProviderConstruction:
    def test_provider_instantiable(self) -> None:
        config = ProviderConfig(name="openrouter", api_key=SecretRef.parse("literal:t"))
        provider = OpenRouterProvider(config, SecretResolver())
        assert provider.name == "openrouter"
