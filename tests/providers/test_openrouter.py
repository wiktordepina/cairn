"""Tests for the OpenRouter provider adapter."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from cairn.config._models import ProviderConfig, SecretRef
from cairn.config._secrets import SecretResolver
from cairn.domain._content import TextBlock
from cairn.domain._enums import StopReason
from cairn.domain._messages import Message
from cairn.domain._provider import (
    GenerationId,
    MessageStop,
    ProviderRequest,
    SystemPromptSegment,
    TextDelta,
    UsageEvent,
)
from cairn.providers._openrouter import OpenRouterProvider


def _simple_request(model: str = "openai/gpt-4o", **overrides: Any) -> ProviderRequest:
    msg = Message(role="user")
    msg.content = [TextBlock(text="hi")]
    return ProviderRequest(model=model, messages=[msg], **overrides)


class TestOpenRouterHeaders:
    def test_default_headers(self) -> None:
        config = ProviderConfig(name="openrouter", api_key=SecretRef.parse("literal:test"))
        provider = OpenRouterProvider(config, SecretResolver())
        headers = provider._build_headers()
        assert "HTTP-Referer" in headers
        assert "X-Title" in headers
        assert headers["X-Title"] == "cairn"

    def test_x_openrouter_title_also_sent(self) -> None:
        """The current canonical header per OR's API ref is
        ``X-OpenRouter-Title``; we send both for the migration window."""
        config = ProviderConfig(name="openrouter", api_key=SecretRef.parse("literal:test"))
        provider = OpenRouterProvider(config, SecretResolver())
        headers = provider._build_headers()
        assert headers.get("X-OpenRouter-Title") == "cairn"

    def test_custom_headers_preserved(self) -> None:
        config = ProviderConfig(
            name="openrouter",
            api_key=SecretRef.parse("literal:test"),
            extra_headers={"HTTP-Referer": "https://custom.example.com", "X-Custom": "val"},
        )
        provider = OpenRouterProvider(config, SecretResolver())
        headers = provider._build_headers()
        assert headers["HTTP-Referer"] == "https://custom.example.com"
        assert headers["X-Title"] == "cairn"  # default still applied
        assert headers["X-Custom"] == "val"

    def test_default_base_url(self) -> None:
        config = ProviderConfig(name="openrouter", api_key=SecretRef.parse("literal:test"))
        provider = OpenRouterProvider(config, SecretResolver())
        # base_url should default to OpenRouter's API
        assert provider._config.base_url is None  # _get_client fills the default


class TestExtraBodyMerge:
    def test_extra_body_passthrough(self) -> None:
        config = ProviderConfig(
            name="openrouter",
            api_key=SecretRef.parse("literal:test"),
            extra_body={
                "provider": {"order": ["anthropic"], "allow_fallbacks": False},
                "transforms": [],
            },
        )
        provider = OpenRouterProvider(config, SecretResolver())
        kwargs = provider._build_kwargs(_simple_request())
        assert kwargs["provider"] == {"order": ["anthropic"], "allow_fallbacks": False}
        assert kwargs["transforms"] == []

    def test_adapter_kwargs_win_over_extra_body(self) -> None:
        """User cannot clobber request invariants like stream / model."""
        config = ProviderConfig(
            name="openrouter",
            api_key=SecretRef.parse("literal:test"),
            extra_body={"stream": False, "model": "evil/override", "max_tokens": 9999},
        )
        provider = OpenRouterProvider(config, SecretResolver())
        kwargs = provider._build_kwargs(_simple_request(model="openai/gpt-4o"))
        assert kwargs["stream"] is True
        assert kwargs["model"] == "openai/gpt-4o"
        assert kwargs["max_tokens"] == 4096  # ProviderRequest default


class TestMiddleOutDisable:
    def test_no_plugin_when_no_cache(self) -> None:
        config = ProviderConfig(name="openrouter", api_key=SecretRef.parse("literal:test"))
        provider = OpenRouterProvider(config, SecretResolver())
        kwargs = provider._build_kwargs(_simple_request())
        assert "plugins" not in kwargs

    def test_plugin_disabled_when_cache_marker_present(self) -> None:
        config = ProviderConfig(name="openrouter", api_key=SecretRef.parse("literal:test"))
        provider = OpenRouterProvider(config, SecretResolver())
        req = _simple_request(system=[SystemPromptSegment(text="identity", cacheable=True)])
        kwargs = provider._build_kwargs(req)
        assert kwargs["plugins"] == [{"id": "context-compression", "enabled": False}]

    def test_plugin_disabled_when_cache_last_message(self) -> None:
        config = ProviderConfig(name="openrouter", api_key=SecretRef.parse("literal:test"))
        provider = OpenRouterProvider(config, SecretResolver())
        req = _simple_request(cache_last_message=True)
        kwargs = provider._build_kwargs(req)
        assert {"id": "context-compression", "enabled": False} in kwargs["plugins"]

    def test_user_plugin_entry_preserved_idempotent(self) -> None:
        """If user already lists context-compression in extra_body, leave it."""
        config = ProviderConfig(
            name="openrouter",
            api_key=SecretRef.parse("literal:test"),
            extra_body={
                "plugins": [{"id": "context-compression", "enabled": True}, {"id": "other"}]
            },
        )
        provider = OpenRouterProvider(config, SecretResolver())
        req = _simple_request(cache_last_message=True)
        kwargs = provider._build_kwargs(req)
        # Adapter must NOT clobber the user's own context-compression entry,
        # even if its ``enabled`` differs.
        cc_entries = [p for p in kwargs["plugins"] if p.get("id") == "context-compression"]
        assert len(cc_entries) == 1
        assert cc_entries[0]["enabled"] is True
        assert any(p.get("id") == "other" for p in kwargs["plugins"])


class TestGenerationIdEvent:
    @pytest.mark.asyncio
    async def test_generation_id_emitted_once(self) -> None:
        """``chunk.id`` is captured on the first chunk that carries it
        and emitted once per stream as a GenerationId event."""
        config = ProviderConfig(name="openrouter", api_key=SecretRef.parse("literal:test"))
        provider = OpenRouterProvider(config, SecretResolver())

        chunks = [
            SimpleNamespace(
                id="gen-abc-123",
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="hi", tool_calls=None),
                        finish_reason=None,
                    )
                ],
                usage=None,
            ),
            SimpleNamespace(
                id="gen-abc-123",
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=" there", tool_calls=None),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2),
            ),
        ]

        async def fake_stream() -> Any:
            for c in chunks:
                yield c

        async def fake_create(**_: Any) -> Any:
            return fake_stream()

        # Inject a fake client so we can drive the stream loop.
        provider._client = SimpleNamespace(  # type: ignore[assignment]
            chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
        )

        events = [ev async for ev in provider.stream(_simple_request())]
        gen_ids = [ev for ev in events if isinstance(ev, GenerationId)]
        assert len(gen_ids) == 1
        assert gen_ids[0].id == "gen-abc-123"

    @pytest.mark.asyncio
    async def test_no_generation_id_when_chunks_lack_id(self) -> None:
        """Some local-compat upstreams may omit ``id``; stream must still work."""
        config = ProviderConfig(name="openrouter", api_key=SecretRef.parse("literal:test"))
        provider = OpenRouterProvider(config, SecretResolver())

        chunks = [
            SimpleNamespace(
                id=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="hi", tool_calls=None),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            ),
        ]

        async def fake_stream() -> Any:
            for c in chunks:
                yield c

        async def fake_create(**_: Any) -> Any:
            return fake_stream()

        provider._client = SimpleNamespace(  # type: ignore[assignment]
            chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
        )

        events = [ev async for ev in provider.stream(_simple_request())]
        assert not [ev for ev in events if isinstance(ev, GenerationId)]


class TestOpenRouterChunkMapping:
    def _make_provider(self) -> OpenRouterProvider:
        config = ProviderConfig(name="openrouter", api_key=SecretRef.parse("literal:test"))
        return OpenRouterProvider(config, SecretResolver())

    def test_text_delta(self) -> None:
        provider = self._make_provider()
        chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="Hello", tool_calls=None),
                    finish_reason=None,
                )
            ],
            usage=None,
        )
        results = provider._map_chunk(chunk, {})
        assert len(results) == 1
        assert isinstance(results[0], TextDelta)

    def test_stop_reason(self) -> None:
        provider = self._make_provider()
        chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=None, tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
        results = provider._map_chunk(chunk, {})
        stops = [r for r in results if isinstance(r, MessageStop)]
        assert len(stops) == 1
        assert stops[0].stop_reason == StopReason.END_TURN

    def test_usage(self) -> None:
        provider = self._make_provider()
        chunk = SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20),
        )
        results = provider._map_chunk(chunk, {})
        usages = [r for r in results if isinstance(r, UsageEvent)]
        assert len(usages) == 1
        assert usages[0].input_tokens == 50

    def test_provider_name(self) -> None:
        provider = self._make_provider()
        assert provider.name == "openrouter"
