# 0039 — Symmetric cache treatment despite asymmetric provider APIs

**Date:** 2026-04-25
**Status:** accepted

## Context

Of the four providers cairn ships:

- **Anthropic** uses explicit `cache_control` markers per content
  block, with separate `cache_creation_input_tokens` (write) and
  `cache_read_input_tokens` (read) on `usage`.
- **OpenAI** caches automatically on stable prefixes ≥ 1024 tokens.
  No markers, no opt-in. `usage.prompt_tokens_details.cached_tokens`
  is the only signal — read-only, no separate write count.
- **OpenRouter** forwards `cache_control` markers to Anthropic-backed
  routes, ignores them on others. Returns either Anthropic-shaped
  (`cache_creation_input_tokens` / `cache_read_input_tokens`) or
  OpenAI-shaped (`prompt_tokens_details.cached_tokens`) usage
  depending on the routed backend.
- **DeepSeek** caches automatically (disk-backed). Returns
  `usage.prompt_cache_hit_tokens` and `prompt_cache_miss_tokens`
  on usage — a third distinct shape, hit-only.

Three obvious approaches:

1. **Per-provider control surface.** Expose Anthropic-style markers
   only when the resolved provider is Anthropic / OpenRouter; gate
   off entirely otherwise. Forces the orchestrator to know provider
   types, branches every cache decision, and surfaces three
   different cache-stat shapes upstream.
2. **Lowest-common-denominator.** Don't expose markers at all; rely
   on auto-cache everywhere it exists. Loses Anthropic's per-segment
   precision (which is the largest cost win), and Anthropic's manual
   markers usually outperform any auto-prefix detection.
3. **Symmetric input, provider-side translation.** What we picked.

## Decision

The control surface is uniform: `ProviderRequest` carries
`cache_tools: bool` + `cache_last_message: bool` flags, and
`system: str | list[SystemPromptSegment]` where
`SystemPromptSegment(cacheable=True)` denotes a cache breakpoint.
Each adapter translates these to whatever the underlying provider
accepts:

- Anthropic: emits `cache_control: ephemeral` markers as designed.
- OpenRouter: emits the same markers, in the same positions; they
  hit on Anthropic-backed routes and are silently ignored on
  others. No branching on backend.
- OpenAI and DeepSeek: ignore the marker flags. Auto-cache picks
  up the same stable-prefix layout because the orchestrator builds
  the request in the same order regardless.

Cache-usage reporting is asymmetric in *what's available* but
symmetric in *what we surface* — every `UsageEvent` carries
`cache_read_tokens` and `cache_write_tokens`. Where a provider does
not separate writes from baseline input (OpenAI, DeepSeek), we set
`cache_write_tokens=0` honestly. We do **not** synthesise a fake
"write" estimate to make providers look uniform.

For OpenRouter we accept either response shape:

```python
# Pseudocode of _usage_from_chunk in _openrouter.py
if usage has cache_creation_input_tokens or cache_read_input_tokens:
    use Anthropic shape
else:
    use OpenAI shape (prompt_tokens_details.cached_tokens)
```

## Consequences

**Pro:**

- One control surface across providers; orchestrator stays
  provider-agnostic.
- Adding a new provider adapter is a translation exercise, not a
  control-flow exercise.
- Future-proof against new cache-supporting backends on OpenRouter
  — when DeepSeek-via-OR or Mistral-via-OR start honouring
  `cache_control`, our requests already carry it.
- Honest cost accounting. `cache_write_tokens=0` for OpenAI /
  DeepSeek reflects API reality; downstream cost computation gets
  the right number.

**Con:**

- Sending `cache_control` to providers that ignore it is technically
  wasted bytes on the wire (negligible compared to message bodies).
- Users comparing cache_write counts across providers will see
  always-zero on some — needs explanation in `docs/observability.md`.
- The cache-usage parser in OpenRouter has to try two shapes; small
  defensive complexity.

## Status

Accepted, shipped at 0.13.0.
