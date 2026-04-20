# 0005 — Per-provider adapters over a unified client

**Date:** 2026-04-20
**Status:** accepted

## Context

Three LLM providers ship in V1: Anthropic, OpenAI, and OpenRouter.
Two options for the implementation shape:

1. **One adapter.** A single `LLMClient` class with branches on the
   provider name, translating each vendor's SDK into cairn's internal
   types.
2. **One adapter per provider.** Each vendor gets its own `Provider`
   implementation, hiding the SDK behind a common protocol.

The providers look similar from a distance — "stream tokens, handle
tool calls, report usage" — but the similarities are shallow. The
differences include:

- Anthropic uses a separate `system` parameter; OpenAI prepends a
  `role:system` message.
- Anthropic inlines `tool_use` blocks in assistant messages; OpenAI
  uses a `tool_calls` array on the assistant message and separate
  `role:tool` result messages.
- Anthropic uses `image` source objects with a `type` discriminator;
  OpenAI uses `image_url` data-URLs.
- OpenRouter adds identity headers (`HTTP-Referer`, `X-Title`) and
  will eventually add fallback-chain config.
- Anthropic has a native token-counting endpoint; OpenAI and OpenRouter
  use `tiktoken` with a fallback.
- Error taxonomies differ: 429 vs. 529 vs. custom shapes.
- Streaming events differ substantially (SSE shape, delta wrapping,
  tool-call ID discovery).

A unified client ends up as a pile of `if provider_name == …:`
branches, each more invasive than the last.

## Decision

One adapter per provider. Each implements the `Provider`
`@runtime_checkable` protocol:

```python
class Provider(Protocol):
    @property
    def name(self) -> str: ...
    def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]: ...
    def count_tokens(self, request: ProviderRequest) -> int: ...
```

Shared helpers live in `_translate.py` (generic mapping utilities) and
in private module internals (`_openai.py` helpers reused by
`_openrouter.py`).

A `ProviderRegistry` — factory plus cache — resolves a `Provider`
instance for a given `ModelConfig` based on `model.provider`. New
vendors register via `ProviderRegistry.register_adapter(name, factory)`.

## Consequences

**Easier:**

- Each adapter is readable as a self-contained file. The
  Anthropic-specific quirks live in `_anthropic.py` and nowhere else.
- Adding a new provider is additive: new adapter, one registration
  call, one block of tests. Nothing existing changes.
- Tests are focused per adapter. Fakes and recordings are
  provider-specific.

**Harder:**

- Code that's genuinely generic (message translation, tool formatting)
  risks duplication across adapters. We pay this cost by extracting
  helpers to `_translate.py` and by having `_openrouter.py` reuse
  `_openai.py` helpers.
- Consistency across adapters is a discipline, not a guarantee. Tests
  and the `Provider` protocol guard the contract; the semantic shape
  of error messages, logging fields, etc. requires conscious
  alignment.

**Ongoing cost:**

- Every behaviour change (e.g. "emit a debug log on retry") has to be
  applied to each adapter rather than to a shared base class. Given
  there are three adapters and the changes are rare, this is fine.
