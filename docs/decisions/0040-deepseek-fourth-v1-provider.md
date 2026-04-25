# 0040 — DeepSeek as the fourth V1 provider

**Date:** 2026-04-25
**Status:** accepted

## Context

The architecture doc `.plan/llm-harness-architecture-v2.md` §4.3
originally committed cairn's V1 surface to three providers:
Anthropic (primary), OpenAI (utility / general), and OpenRouter
(catch-all). Google's `google-genai` SDK was deferred to V2 for
"different enough" API shape.

While shipping the prompt-caching brick we needed to handle a
third distinct cache-usage shape — `prompt_cache_hit_tokens` /
`prompt_cache_miss_tokens` — that no shipping provider used.
DeepSeek's API:

- Is OpenAI-compatible at the chat-completions level; reuses the
  OpenAI SDK pointed at `https://api.deepseek.com/v1`.
- Auto-caches like OpenAI but with the third usage shape.
- Bills cache hits at ~10 % of input rate (vs. OpenAI's 50%) — caching
  pays back from the first re-use rather than the second.
- Ships `deepseek-reasoner`, an R1-class reasoning model that's
  the cheapest serious "ask the smart model" delegation target on
  the market.

The pull was: handle the third cache shape *anyway* (the dual-shape
parser in OpenRouter already had to deal with two), pay ~80 lines
to write a DeepSeek adapter, and unlock a useful new delegation
target. The push-back was scope creep on a caching brick.

We considered:

- **Defer to V2 with Google.** Keeps the brick narrow but means the
  user can't use DeepSeek today, and the cache plumbing has to know
  about a usage shape it has no test coverage for.
- **Add a generic "openai-compatible" provider with config-driven
  cache shape.** Avoids a dedicated adapter but creates a
  configuration footgun (users wire usage parsing via TOML?). Not
  worth the abstraction.
- **Add DeepSeek now.** What we picked.

## Decision

Ship `cairn.providers._deepseek.DeepSeekProvider` as a fourth V1
adapter alongside the prompt-caching brick (0.13.0). Marginal cost:

- ~200 lines of adapter code, 80% of which is mechanical reuse of
  the OpenAI format helpers.
- One new test file (`tests/providers/test_deepseek.py`, 11 tests)
  covering registration, base URL, format-helper sharing, the new
  cache-usage shape, and reasoning-content drop posture.
- One ADR (this one).
- Documentation extension to `docs/providers.md` and the per-model
  table in `docs/prompt-caching.md`.

Reasoning content from `deepseek-reasoner` (the `reasoning_content`
field on streamed deltas) is dropped silently in V1 — we have no
streaming thinking-event type yet, and the Anthropic adapter takes
the same posture for thinking deltas. Adding a `ThinkingDelta`
event would ripple through the orchestrator, observers, and UI
widgets; out of scope for this brick.

The Google adapter remains V2 — its API shape (Vertex AI / GenAI)
is genuinely different and warrants its own design pass.

## Consequences

**Pro:**

- V1 users gain access to a cheap, capable companion / utility model
  pair (`deepseek-chat`) and a cheap reasoning-class delegation
  target (`deepseek-reasoner`).
- The third cache-usage shape gets test coverage and lives in real
  product code, not a TODO.
- Validates the "symmetric input, provider-side translation"
  approach (ADR 0039) against a fourth distinct provider.

**Con:**

- Reasoning content is dropped silently. Users running the reasoner
  see a quiet model — no thinking trace surfaces. We document
  this in `docs/providers.md`.
- DeepSeek's tokenizer is not tiktoken-compatible; `count_tokens`
  falls back to the character-based estimate.
- We have one more SDK code path to keep working. The OpenAI SDK is
  already a dependency, so the marginal maintenance cost is low.

## Status

Accepted, shipped at 0.13.0.
