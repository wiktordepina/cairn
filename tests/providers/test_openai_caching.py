"""Tests for OpenAI prompt-caching shape (auto-cache, no markers)."""

from __future__ import annotations

from types import SimpleNamespace

from cairn.config._models import ProviderConfig, SecretRef
from cairn.config._secrets import SecretResolver
from cairn.domain._provider import SystemPromptSegment, UsageEvent
from cairn.providers._openai import (
    OpenAIProvider,
    _usage_from_chunk,
    flatten_system,
    format_messages,
    format_tools,
)


class TestFlattenSystem:
    def test_string_passes_through(self) -> None:
        assert flatten_system("hello") == "hello"

    def test_none_passes_through(self) -> None:
        assert flatten_system(None) is None

    def test_segment_list_joined_with_blank_line(self) -> None:
        segs = [
            SystemPromptSegment(text="profile", cacheable=True),
            SystemPromptSegment(text="session", cacheable=True),
        ]
        assert flatten_system(segs) == "profile\n\nsession"

    def test_empty_segments_skipped(self) -> None:
        segs = [
            SystemPromptSegment(text="a", cacheable=True),
            SystemPromptSegment(text="", cacheable=True),
            SystemPromptSegment(text="b", cacheable=True),
        ]
        assert flatten_system(segs) == "a\n\nb"


class TestNoMarkersEmitted:
    """Cache flags on ProviderRequest are no-ops on OpenAI — no
    cache_control field anywhere in the request body."""

    def test_format_tools_no_cache_field(self) -> None:
        from cairn.domain._provider import ToolDefinition

        tools = [ToolDefinition(name="t", description="t", input_schema={})]
        result = format_tools(tools)
        assert all("cache_control" not in t for t in result)
        # function dict is also untouched
        assert "cache_control" not in result[0]["function"]

    def test_format_messages_with_segment_system_no_cache_field(self) -> None:
        from cairn.domain._content import TextBlock
        from cairn.domain._messages import Message

        msg = Message(role="user")
        msg.content = [TextBlock(text="hi")]
        segs = [SystemPromptSegment(text="profile", cacheable=True)]
        result = format_messages([msg], system=segs)

        # No cache_control on system message
        assert "cache_control" not in result[0]
        # No cache_control anywhere downstream
        for m in result:
            assert "cache_control" not in m


class TestUsageWithCache:
    def test_cached_tokens_populates_cache_read(self) -> None:
        usage = SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=200,
            prompt_tokens_details=SimpleNamespace(cached_tokens=512),
        )
        ev = _usage_from_chunk(usage)
        assert isinstance(ev, UsageEvent)
        assert ev.input_tokens == 1000
        assert ev.output_tokens == 200
        assert ev.cache_read_tokens == 512
        assert ev.cache_write_tokens == 0

    def test_missing_prompt_tokens_details_safe(self) -> None:
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10)
        ev = _usage_from_chunk(usage)
        assert ev.cache_read_tokens == 0
        assert ev.cache_write_tokens == 0

    def test_none_cached_tokens_coerced_zero(self) -> None:
        usage = SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            prompt_tokens_details=SimpleNamespace(cached_tokens=None),
        )
        ev = _usage_from_chunk(usage)
        assert ev.cache_read_tokens == 0


class TestStreamOptionsIncludeUsage:
    """Without stream_options.include_usage=True, OpenAI omits usage in
    streamed mode. Assert we always set it."""

    def test_kwargs_built_with_include_usage(self) -> None:
        # The flag is a constant in the adapter — we exercise the
        # construction path indirectly by reading the body of stream().
        # A black-box check would require mocking the SDK, which is
        # heavier than this brick warrants. Source-grep is cheap.
        from pathlib import Path

        src = Path(__file__).resolve().parents[2] / "src/cairn/providers/_openai.py"
        text = src.read_text(encoding="utf-8")
        assert '"stream_options": {"include_usage": True}' in text


class TestProviderConstruction:
    def test_provider_instantiable(self) -> None:
        config = ProviderConfig(name="openai", api_key=SecretRef.parse("literal:t"))
        provider = OpenAIProvider(config, SecretResolver())
        assert provider.name == "openai"
