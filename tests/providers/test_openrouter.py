"""Tests for the OpenRouter provider adapter."""

from __future__ import annotations

from types import SimpleNamespace

from cairn.config._models import ProviderConfig, SecretRef
from cairn.config._secrets import SecretResolver
from cairn.domain._enums import StopReason
from cairn.domain._provider import MessageStop, TextDelta, UsageEvent
from cairn.providers._openrouter import OpenRouterProvider


class TestOpenRouterHeaders:
    def test_default_headers(self) -> None:
        config = ProviderConfig(name="openrouter", api_key=SecretRef.parse("literal:test"))
        provider = OpenRouterProvider(config, SecretResolver())
        headers = provider._build_headers()
        assert "HTTP-Referer" in headers
        assert "X-Title" in headers
        assert headers["X-Title"] == "cairn"

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
