# 0038 — Four cache breakpoints, fixed positions

**Date:** 2026-04-25
**Status:** accepted

## Context

Anthropic's prompt-cache API caps the number of `cache_control`
markers per request at **4**. The other providers we ship either
accept the same markers (OpenRouter on Anthropic-backed routes) or
ignore them entirely in favour of automatic caching (OpenAI,
DeepSeek). We needed a placement strategy that:

1. Stays within the four-marker cap on Anthropic.
2. Picks positions where the *cached span* has predictable
   stability so cache hits actually accrue.
3. Is uniform across providers — the orchestrator should not have
   to reason about per-provider marker shapes.
4. Survives the typical companion conversation cadence (one turn
   every few minutes, sometimes back-to-back).

We considered several alternatives:

- **One marker, end of system prompt only.** Caches the system
  prompt but not the tool catalogue or message history. Wastes
  three of the four available markers and leaves the largest
  growing span — message history — uncached.
- **One marker per recent message (LRU).** Adapts to where activity
  is happening, but the cache invalidates on every turn because the
  most recent message is always different. No steady-state win.
- **Per-segment dynamic markers.** Mark whichever spans the context
  manager just touched. Coupling between context-manager state and
  the request body is high; debugging cache misses becomes opaque.
- **Fixed positions, four markers.** What we picked.

## Decision

We place four `cache_control` markers at fixed, semantically
meaningful positions:

| # | Position | Spans | Lifetime |
|---|---|---|---|
| 1 | end of profile-stable system segment | identity + user_context + project_conventions + memory_index | hours — invalidates on profile-doc edits |
| 2 | end of session-stable system segment | retrieved_memories + persona_system_prompt | this session — invalidates on memory retrieval drift |
| 3 | last tool definition | full tool catalogue | this session — invalidates on session-type change |
| 4 | last content block of last message | everything in the request, including this message | next turn within Anthropic's 5-min TTL |

The positions are encoded structurally:

- The system prompt is split by `StandardContextManager` into one
  or two `SystemPromptSegment`s with `cacheable=True`.
- `cache_tools` and `cache_last_message` are bool flags on
  `ProviderRequest` set by the orchestrator from
  `ModelConfig.supports_prompt_cache`.

Provider adapters translate these into provider-native shapes:

- **Anthropic** — `cache_control: ephemeral` on the relevant content
  block / tool / message part.
- **OpenRouter** — same as Anthropic on Anthropic-backed routes;
  silently ignored on others.
- **OpenAI / DeepSeek** — flags are no-ops; auto-cache handles
  things via stable-prefix detection.

A defensive cap enforcer in each adapter drops oldest markers
first with a `WARNING` if upstream code somehow exceeds 4 — the
adapter never propagates a 400 to the model layer.

## Consequences

**Pro:**

- Predictable. The cache shape is the same on every turn; effective
  cache-hit rate is straightforward to reason about.
- Maximises the cap. Three of the four markers cover stable spans;
  the fourth is the rolling tail that gives the *next* turn a hit
  on everything sent in *this* turn.
- Provider-uniform interface. Adapters translate their own way; the
  orchestrator doesn't branch on provider name.

**Con:**

- Inflexible. If a future provider supports more than 4 markers we
  can't take advantage without a redesign. (We have no such
  provider today.)
- Profile-stable segment churn punishes the cache disproportionately.
  A `MEMORY.md` edit invalidates marker #1 — typically the largest
  cached span. We document this as a troubleshooting tip rather
  than try to be cleverer about partial invalidation.
- Worst case (1-turn session, never cached again) pays a ~25% cache-
  write surcharge over the bare-input rate. We document the cost
  envelope; for steady-state usage the savings dwarf the surcharge.

## Status

Accepted, shipped at 0.13.0. Revisit when V2 introduces longer
delegation chains or multi-session shared caching.
