"""Cairn provider layer — vendor-agnostic LLM provider abstraction."""

from cairn.providers._anthropic import AnthropicProvider
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
from cairn.providers._registry import ProviderNotConfiguredError, ProviderRegistry

__all__ = [
    # Protocol
    "Provider",
    # Adapters
    "AnthropicProvider",
    "OpenAIProvider",
    "OpenRouterProvider",
    # Registry
    "ProviderNotConfiguredError",
    "ProviderRegistry",
    # Errors
    "AuthenticationError",
    "ModelNotAvailableError",
    "ProviderError",
    "ProviderOverloadedError",
    "RateLimitError",
]
