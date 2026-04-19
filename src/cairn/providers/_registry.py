"""Provider registry — factory and cache for provider instances."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cairn.config._models import CairnConfig, ModelConfig
    from cairn.config._secrets import SecretResolver
    from cairn.providers._protocol import Provider


class ProviderNotConfiguredError(Exception):
    """Raised when a provider name has no matching config or adapter."""


class ProviderRegistry:
    """Creates and caches Provider instances from config.

    The registry maps provider names (from TOML config keys) to adapter
    classes, creates instances lazily, and caches them for reuse.
    """

    def __init__(self, config: CairnConfig, secret_resolver: SecretResolver) -> None:
        self._config = config
        self._secret_resolver = secret_resolver
        self._cache: dict[str, Provider] = {}
        self._factories: dict[str, type[Any]] = _default_factories()

    def for_model(self, model_config: ModelConfig) -> Provider:
        """Get the provider for a model (by its ``provider`` field)."""
        return self.by_name(model_config.provider)

    def by_name(self, name: str) -> Provider:
        """Get (or create) a provider by its config name."""
        if name in self._cache:
            return self._cache[name]

        provider_config = self._config.providers.get(name)
        if provider_config is None:
            raise ProviderNotConfiguredError(
                f"No provider config for {name!r}. "
                f"Configured providers: {sorted(self._config.providers.keys())}"
            )

        factory = self._factories.get(name)
        if factory is None:
            raise ProviderNotConfiguredError(
                f"No adapter registered for provider {name!r}. "
                f"Available adapters: {sorted(self._factories.keys())}"
            )

        provider: Provider = factory(provider_config, self._secret_resolver)
        self._cache[name] = provider
        return provider

    def register_adapter(self, name: str, factory: type[Any]) -> None:
        """Register a custom provider adapter class."""
        self._factories[name] = factory


def _default_factories() -> dict[str, type[Any]]:
    """Return the built-in adapter mapping.

    Imports are deferred to avoid loading SDKs until actually needed.
    """
    from cairn.providers._anthropic import AnthropicProvider
    from cairn.providers._openai import OpenAIProvider
    from cairn.providers._openrouter import OpenRouterProvider

    return {
        "anthropic": AnthropicProvider,
        "openai": OpenAIProvider,
        "openrouter": OpenRouterProvider,
    }
