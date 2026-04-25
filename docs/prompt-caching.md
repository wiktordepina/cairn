# Prompt caching

Cairn caches stable spans of every turn so the next turn pays only for
what actually changed. With Anthropic's primary model it is the single
biggest cost lever you have — order-of-magnitude reductions on input
tokens for back-to-back turns. This page describes what gets cached,
how to read the stats, and how to tune it.

## What gets cached

Every provider request Cairn builds has up to four cache breakpoints
arranged in fixed positions. The shape of a typical request:

```
[ system prompt ]
   ├─  identity + user_context + project_conventions + memory_index   ← marker #1 (profile-stable)
   └─  retrieved_memories + persona_system_prompt                     ← marker #2 (session-stable)
[ tools ]
   └─  …, …, …, last_tool                                             ← marker #3 (session-stable)
[ messages ]
   └─  …, last user/tool message                                      ← marker #4 (growing tail)
```

| Marker | Spans | Lifetime | Invalidates when |
|---|---|---|---|
| #1 profile-stable | identity + user context + project conventions + MEMORY.md index | hours | you edit `soul_document.md`, `user_context.md`, `MEMORY.md`, or any `AGENTS.md` / `CLAUDE.md` / `CAIRN.md` in the active project |
| #2 session-stable | retrieved memories + persona prompt | this session | retrieved-memory set changes (per-turn) |
| #3 tool catalogue | the full tool list | this session | session type changes (companion → persona, etc.) |
| #4 growing tail | everything up to and including the last message | next turn within the cache TTL | TTL elapses (5 min on Anthropic standard tier) |

This means the **second turn of a session** typically reads ~90% of its
input tokens from cache instead of re-tokenising them.

## Cost impact (worked example)

For a back-to-back turn against `claude-sonnet-4-6` (3 USD / Mtok input,
3.75 USD / Mtok cache write, 0.30 USD / Mtok cache read):

| Segment | Tokens | Without cache | With cache (read) |
|---|--:|--:|--:|
| Identity + user context | 600 | $0.0018 | $0.000180 |
| Memory index + retrieved | 1500 | $0.0045 | $0.000450 |
| Project conventions | 2000 | $0.0060 | $0.000600 |
| Tool catalogue (4 built-ins) | 1200 | $0.0036 | $0.000360 |
| Persona prompt | 400 | $0.0012 | $0.000120 |
| **Stable head** | **5700** | **$0.0171** | **$0.0017** |

That's ~10× cheaper on the stable head, which on a busy session is
where most of the input cost lives. Cache writes pay back after the
first re-use; on Anthropic standard-tier the cache stays warm for 5
minutes between turns.

## Per-provider support

Caching is gated by `ModelConfig.supports_prompt_cache`. Cairn ships
sensible defaults; flip the flag in your `config.toml` if you change a
model's pricing or back-end.

| Provider | Supports cache | Mechanism | Read-only? |
|---|---|---|---|
| **Anthropic** | yes | Native `cache_control` markers, four breakpoints | no — write tokens billed |
| **OpenRouter** (Anthropic-backed routes) | yes | `cache_control` passthrough | no |
| **OpenRouter** (other backends) | yes (no-op) | Markers ignored silently | n/a |
| **OpenAI** | yes | Automatic on stable prefixes ≥ 1024 tokens | yes — cache hits 50% of input rate |
| **DeepSeek** | yes | Automatic disk-based context cache | yes — cache hits ~10% of input rate |
| **Local OpenAI-compatible** (llama.cpp, vLLM, LM Studio) | **no** (default) | n/a | n/a — flip to `true` only if your stack echoes `cached_tokens` |

When a model's `supports_prompt_cache` is `false`, Cairn keeps the
legacy flat-string system-prompt path and never sends cache markers —
safe for local servers that reject unknown fields.

## Reading the stats

### `/context` slash command

In the TUI, `/context` shows a per-turn breakdown including
cache-read and cache-write tokens. Numbers come from the last
streamed `UsageEvent` of the previous turn.

### Log file

Every persisted `model_usage` row carries `cache_read_tokens` and
`cache_write_tokens`. To inspect:

```sql
SELECT operation, input_tokens, output_tokens,
       cache_read_tokens, cache_write_tokens, cost_usd
FROM model_usage
WHERE session_id = '<your-session-id>'
ORDER BY recorded_at;
```

The structured event log (see [Observability](observability.md)) does
not duplicate cache fields per record — usage is recorded against the
turn rather than every event.

## Troubleshooting

### "Cache writes spike on every turn, no reads"

Most common cause: something in your **profile-stable** segment
changes between turns. Check, in order:

1. `MEMORY.md` — was it just edited? Each change invalidates marker #1.
2. Project conventions — did your editor write a no-op timestamp on
   `AGENTS.md`? File watchers can be noisy.
3. Soul doc / user context — same pattern.

Marker #1 is meant to outlive whole working sessions; if it churns
every turn you'll pay for cache writes without ever getting a read.

### "Cache hits are smaller than expected on the second turn"

Check that fewer than 5 minutes elapsed between turns (Anthropic's
default TTL). If you took a coffee break, the cache will have expired.

### "OpenAI / DeepSeek `cached_tokens` is always zero"

OpenAI's auto-cache only kicks in for prompts ≥ 1024 tokens; very short
turns won't benefit. DeepSeek's cache is disk-backed and may take a
few seconds to warm on the first call after a long idle period.

### "Local model rejects the request"

Set `supports_prompt_cache = false` for that model in your
`config.toml`. Local OpenAI-compatible servers don't always tolerate
unknown fields; this disables marker emission.

## Tuning knobs

V1 deliberately ships no TTL knob. The Anthropic default 5-minute TTL
fits the typical companion conversation cadence. If you find yourself
wanting the 1-hour tier, file an issue with your usage pattern — it is
a one-line addition to `ModelConfig` but adds a configuration footgun.

For the algorithmic detail of *why* we picked these four positions,
see [ADR 0038](decisions/0038-four-cache-breakpoints.md). The
treatment of provider asymmetries (Anthropic markers, OpenAI / DeepSeek
auto-cache, OpenRouter dual-shape) is the subject of
[ADR 0039](decisions/0039-symmetric-cache-treatment.md). DeepSeek
adoption is rationalised in
[ADR 0040](decisions/0040-deepseek-fourth-v1-provider.md).
