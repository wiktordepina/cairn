"""Tests for Provider protocol conformance."""

from __future__ import annotations

import pytest

from cairn.config._models import ProviderConfig, SecretRef
from cairn.config._secrets import SecretResolver
from cairn.providers._anthropic import AnthropicProvider
from cairn.providers._deepseek import DeepSeekProvider
from cairn.providers._openai import OpenAIProvider
from cairn.providers._openrouter import OpenRouterProvider
from cairn.providers._protocol import (
    AuthenticationError,
    ModelNotAvailableError,
    Provider,
    ProviderError,
    ProviderOverloadedError,
    RateLimitError,
)


def _make_config(name: str) -> ProviderConfig:
    return ProviderConfig(name=name, api_key=SecretRef.parse("literal:test"))


class TestProtocolConformance:
    def test_anthropic_satisfies_protocol(self) -> None:
        provider = AnthropicProvider(_make_config("anthropic"), SecretResolver())
        assert isinstance(provider, Provider)

    def test_openai_satisfies_protocol(self) -> None:
        provider = OpenAIProvider(_make_config("openai"), SecretResolver())
        assert isinstance(provider, Provider)

    def test_openrouter_satisfies_protocol(self) -> None:
        provider = OpenRouterProvider(_make_config("openrouter"), SecretResolver())
        assert isinstance(provider, Provider)

    def test_provider_name(self) -> None:
        provider = AnthropicProvider(_make_config("anthropic"), SecretResolver())
        assert provider.name == "anthropic"

    def test_deepseek_satisfies_protocol(self) -> None:
        provider = DeepSeekProvider(_make_config("deepseek"), SecretResolver())
        assert isinstance(provider, Provider)


class TestBalanceDefault:
    @pytest.mark.asyncio
    async def test_anthropic_returns_none(self) -> None:
        provider = AnthropicProvider(_make_config("anthropic"), SecretResolver())
        assert await provider.balance() is None

    @pytest.mark.asyncio
    async def test_openai_returns_none(self) -> None:
        provider = OpenAIProvider(_make_config("openai"), SecretResolver())
        assert await provider.balance() is None

    @pytest.mark.asyncio
    async def test_deepseek_stub_returns_none_until_phase_5(self) -> None:
        provider = DeepSeekProvider(_make_config("deepseek"), SecretResolver())
        assert await provider.balance() is None

    @pytest.mark.asyncio
    async def test_openrouter_stub_returns_none_until_phase_5(self) -> None:
        provider = OpenRouterProvider(_make_config("openrouter"), SecretResolver())
        assert await provider.balance() is None


class TestErrorHierarchy:
    def test_base_error(self) -> None:
        err = ProviderError("fail", provider="test")
        assert err.provider == "test"
        assert err.retryable is False

    def test_auth_error(self) -> None:
        err = AuthenticationError("bad key", provider="anthropic")
        assert isinstance(err, ProviderError)
        assert err.retryable is False

    def test_rate_limit_error(self) -> None:
        err = RateLimitError("slow down", provider="openai", retry_after=30.0)
        assert isinstance(err, ProviderError)
        assert err.retryable is True
        assert err.retry_after == 30.0

    def test_model_not_available(self) -> None:
        err = ModelNotAvailableError("no such model", provider="anthropic")
        assert isinstance(err, ProviderError)
        assert err.retryable is False

    def test_provider_overloaded(self) -> None:
        err = ProviderOverloadedError("overloaded", provider="openai")
        assert isinstance(err, ProviderError)
        assert err.retryable is True
