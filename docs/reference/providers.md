# Providers — API reference

The public surface of the provider layer: the `Provider` protocol, concrete
adapters for each supported vendor, the registry that resolves a provider by
name, and the error hierarchy that callers can catch.

For a narrative overview of how providers slot into the turn loop, see
[Providers](../providers.md) and [Architecture](../architecture.md).

## Protocol

::: cairn.providers.Provider

## Adapters

::: cairn.providers.AnthropicProvider

::: cairn.providers.OpenAIProvider

::: cairn.providers.OpenRouterProvider

## Registry

::: cairn.providers.ProviderRegistry

## Errors

::: cairn.providers.ProviderError

::: cairn.providers.AuthenticationError

::: cairn.providers.ModelNotAvailableError

::: cairn.providers.ProviderOverloadedError

::: cairn.providers.RateLimitError

::: cairn.providers.ProviderNotConfiguredError
