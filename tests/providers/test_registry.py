"""Tests for ProviderRegistry."""

from __future__ import annotations

from typing import Any

import pytest

from cairn.config._models import CairnConfig
from cairn.config._secrets import SecretResolver
from cairn.providers._anthropic import AnthropicProvider
from cairn.providers._openai import OpenAIProvider
from cairn.providers._openrouter import OpenRouterProvider
from cairn.providers._registry import ProviderNotConfiguredError, ProviderRegistry
from tests.config.conftest import full_config_dict


def _make_config_with_providers(*names: str) -> CairnConfig:
    """Create a CairnConfig with the given provider names."""
    data = full_config_dict()
    data["providers"] = {}
    for name in names:
        data["providers"][name] = {"name": name, "api_key": "env:TEST_CAIRN_KEY"}
    return CairnConfig.model_validate(data)


class TestProviderRegistry:
    def test_by_name_anthropic(self) -> None:
        config = _make_config_with_providers("anthropic")
        registry = ProviderRegistry(config, SecretResolver())
        provider = registry.by_name("anthropic")
        assert isinstance(provider, AnthropicProvider)
        assert provider.name == "anthropic"

    def test_by_name_openai(self) -> None:
        config = _make_config_with_providers("openai")
        registry = ProviderRegistry(config, SecretResolver())
        provider = registry.by_name("openai")
        assert isinstance(provider, OpenAIProvider)

    def test_by_name_openrouter(self) -> None:
        config = _make_config_with_providers("openrouter")
        registry = ProviderRegistry(config, SecretResolver())
        provider = registry.by_name("openrouter")
        assert isinstance(provider, OpenRouterProvider)

    def test_caches_instances(self) -> None:
        config = _make_config_with_providers("anthropic")
        registry = ProviderRegistry(config, SecretResolver())
        a = registry.by_name("anthropic")
        b = registry.by_name("anthropic")
        assert a is b

    def test_unknown_provider_raises(self) -> None:
        config = _make_config_with_providers("anthropic")
        registry = ProviderRegistry(config, SecretResolver())
        with pytest.raises(ProviderNotConfiguredError, match="nonexistent"):
            registry.by_name("nonexistent")

    def test_no_adapter_for_configured_provider(self) -> None:
        """Provider is in config but no adapter is registered."""
        config = _make_config_with_providers("some-unknown-provider")
        registry = ProviderRegistry(config, SecretResolver())
        with pytest.raises(ProviderNotConfiguredError, match="No adapter"):
            registry.by_name("some-unknown-provider")

    def test_for_model_delegates(self) -> None:
        config = _make_config_with_providers("anthropic")
        registry = ProviderRegistry(config, SecretResolver())
        model = config.models[0]  # has provider="anthropic"
        provider = registry.for_model(model)
        assert isinstance(provider, AnthropicProvider)

    def test_register_custom_adapter(self) -> None:
        config = _make_config_with_providers("custom")
        registry = ProviderRegistry(config, SecretResolver())

        class CustomProvider:
            def __init__(self, config: Any, resolver: Any) -> None:
                self._name = config.name

            @property
            def name(self) -> str:
                return self._name

        registry.register_adapter("custom", CustomProvider)
        provider = registry.by_name("custom")
        assert provider.name == "custom"
