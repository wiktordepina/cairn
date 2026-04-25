"""Tests for the DeepSeek provider adapter (new in 0.13.0)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from cairn.config._models import CairnConfig, ProviderConfig, SecretRef
from cairn.config._secrets import SecretResolver
from cairn.domain._content import TextBlock
from cairn.domain._messages import Message
from cairn.domain._provider import (
    SystemPromptSegment,
    TextDelta,
    UsageEvent,
)
from cairn.providers._deepseek import DeepSeekProvider, _usage_from_chunk
from cairn.providers._registry import ProviderRegistry

if TYPE_CHECKING:
    pass


def _provider() -> DeepSeekProvider:
    config = ProviderConfig(name="deepseek", api_key=SecretRef.parse("literal:t"))
    return DeepSeekProvider(config, SecretResolver())


class TestRegistration:
    def test_registry_resolves_deepseek(self) -> None:
        cfg = CairnConfig(
            schema_version=1,
            active_profile="default",
            providers={
                "deepseek": ProviderConfig(
                    name="deepseek", api_key=SecretRef.parse("literal:t")
                ),
            },
        )
        registry = ProviderRegistry(cfg, SecretResolver())
        provider = registry.by_name("deepseek")
        assert isinstance(provider, DeepSeekProvider)
        # Cached on subsequent fetch
        assert registry.by_name("deepseek") is provider


class TestBaseUrl:
    def test_default_base_url(self) -> None:
        provider = _provider()
        # The base URL is set lazily on first client construction. We
        # can verify the constant directly without spinning the SDK.
        from cairn.providers import _deepseek

        assert _deepseek._DEFAULT_BASE_URL == "https://api.deepseek.com/v1"

    def test_config_base_url_override(self) -> None:
        config = ProviderConfig(
            name="deepseek",
            api_key=SecretRef.parse("literal:t"),
            base_url="https://custom.example/v1",
        )
        provider = DeepSeekProvider(config, SecretResolver())
        # Config respected (we can't easily inspect the lazy SDK client
        # without async setup, so check the underlying config).
        assert provider._config.base_url == "https://custom.example/v1"


class TestFormatHelpersShared:
    """DeepSeek reuses the OpenAI format helpers — sanity check the
    same outputs come out for identical inputs."""

    def test_format_messages_segment_system_flattens(self) -> None:
        from cairn.providers._deepseek import format_messages

        msg = Message(role="user")
        msg.content = [TextBlock(text="hi")]
        segs = [
            SystemPromptSegment(text="profile", cacheable=True),
            SystemPromptSegment(text="session", cacheable=True),
        ]
        result = format_messages([msg], system=segs)
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "profile\n\nsession"

    def test_no_cache_control_emitted(self) -> None:
        from cairn.domain._provider import ToolDefinition
        from cairn.providers._deepseek import format_messages, format_tools

        msg = Message(role="user")
        msg.content = [TextBlock(text="hi")]
        segs = [SystemPromptSegment(text="x", cacheable=True)]
        msg_result = format_messages([msg], system=segs)
        tools_result = format_tools([
            ToolDefinition(name="t", description="t", input_schema={}),
        ])
        for m in msg_result:
            assert "cache_control" not in m
        for t in tools_result:
            assert "cache_control" not in t
            assert "cache_control" not in t.get("function", {})


class TestUsageWithCache:
    def test_prompt_cache_hit_tokens_populates_cache_read(self) -> None:
        usage = SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=200,
            prompt_cache_hit_tokens=750,
            prompt_cache_miss_tokens=250,
        )
        ev = _usage_from_chunk(usage)
        assert isinstance(ev, UsageEvent)
        assert ev.input_tokens == 1000
        assert ev.output_tokens == 200
        assert ev.cache_read_tokens == 750
        assert ev.cache_write_tokens == 0

    def test_missing_cache_fields_safe(self) -> None:
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10)
        ev = _usage_from_chunk(usage)
        assert ev.cache_read_tokens == 0
        assert ev.cache_write_tokens == 0

    def test_none_cache_field_coerced_zero(self) -> None:
        usage = SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            prompt_cache_hit_tokens=None,
        )
        ev = _usage_from_chunk(usage)
        assert ev.cache_read_tokens == 0


class TestReasoningContentDropped:
    """deepseek-reasoner emits ``reasoning_content`` deltas. V1 drops
    them (matches Anthropic's posture for thinking-block deltas)."""

    def test_reasoning_only_chunk_emits_no_event(self) -> None:
        provider = _provider()
        chunk = SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content="thinking out loud...",
                        tool_calls=None,
                    ),
                    finish_reason=None,
                )
            ],
        )
        results = provider._map_chunk(chunk, {})
        assert results == []

    def test_reasoning_plus_text_emits_only_text(self) -> None:
        provider = _provider()
        chunk = SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content="answer",
                        reasoning_content="thought",
                        tool_calls=None,
                    ),
                    finish_reason=None,
                )
            ],
        )
        results = provider._map_chunk(chunk, {})
        assert len(results) == 1
        assert isinstance(results[0], TextDelta)
        assert results[0].text == "answer"


class TestUsageFlowsThroughMapChunk:
    def test_usage_chunk_emits_usage_event(self) -> None:
        provider = _provider()
        chunk = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=50,
                prompt_cache_hit_tokens=80,
                prompt_cache_miss_tokens=20,
            ),
            choices=[],
        )
        results = provider._map_chunk(chunk, {})
        assert len(results) == 1
        ev = results[0]
        assert isinstance(ev, UsageEvent)
        assert ev.cache_read_tokens == 80
