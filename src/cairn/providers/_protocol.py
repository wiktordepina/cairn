"""Provider protocol and error hierarchy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from cairn.domain._provider import ProviderEvent, ProviderRequest


@runtime_checkable
class Provider(Protocol):
    """Vendor-agnostic interface for LLM providers.

    Implementations translate between cairn's domain types and
    provider-specific SDK formats. The protocol has two methods:

    - ``stream()`` — the primary path; async generator yielding events
    - ``count_tokens()`` — token estimation for budget tracking
    """

    @property
    def name(self) -> str:
        """Provider name (matches ProviderConfig key, e.g. ``'anthropic'``)."""
        ...

    def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        """Stream a completion, yielding domain events.

        Declared as a plain ``def`` rather than ``async def`` because the
        returned ``AsyncIterator`` is iterated directly with ``async for``;
        no ``await`` is needed before iteration. Implementations are
        typically written as ``async def ... yield ...`` — Python treats
        those as async-generator functions whose call returns an
        AsyncIterator, matching this signature.
        """
        ...

    async def count_tokens(self, request: ProviderRequest) -> int:
        """Estimate token count for a request."""
        ...


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    """Base for all provider errors."""

    def __init__(self, message: str, *, provider: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


class AuthenticationError(ProviderError):
    """API key invalid or missing."""

    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message, provider=provider, retryable=False)


class RateLimitError(ProviderError):
    """Rate-limited by the provider."""

    def __init__(self, message: str, *, provider: str, retry_after: float | None = None) -> None:
        super().__init__(message, provider=provider, retryable=True)
        self.retry_after = retry_after


class ModelNotAvailableError(ProviderError):
    """Requested model doesn't exist or isn't accessible."""

    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message, provider=provider, retryable=False)


class ProviderOverloadedError(ProviderError):
    """Provider is overloaded (529/503)."""

    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message, provider=provider, retryable=True)
