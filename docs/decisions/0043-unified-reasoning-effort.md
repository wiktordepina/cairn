# 0043 — Unified `reasoning_effort` knob across providers

**Date:** 2026-04-26
**Status:** accepted

## Context

Three of the four V1 providers expose a "reasoning depth" control
under different names and shapes:

- **OpenAI** o-series and gpt-5-class models accept a
  `reasoning_effort` enum (`"none" | "minimal" | "low" | "medium"
  | "high" | "xhigh"`) — intrinsic to the model family.
- **Anthropic** extended thinking exposes a numeric
  `thinking.budget_tokens` opt-in per request — must be `<
  max_tokens` and `>= 1024`.
- **DeepSeek** V4-pro-class models accept a `reasoning_effort` enum
  via `extra_body`; `deepseek-reasoner` always thinks (no flag).
- **OpenRouter** forwards whichever shape the routed upstream
  expects.

The pull was: callers shouldn't need a provider-specific switch
to ask for "more thinking, please" on whichever model their
profile selected. The push-back was that fixed cross-provider
mappings can mis-translate the user's intent (a 1024-token
Anthropic budget is "minimal" reasoning compared to OpenAI's
`"low"`-tier o-series).

## Decision

Add a single `reasoning_effort: str | None` field to
`ProviderRequest` taking the OpenAI-style enum:
`"none" | "minimal" | "low" | "medium" | "high" | "xhigh"`.

Each adapter translates per its own shape:

| Adapter | Translation |
|---|---|
| OpenAI | Pass through verbatim, only when the model is in the reasoning family (o-series, gpt-5-class). Silently dropped on chat models so callers can set it unconditionally. |
| Anthropic | Map *proportionally* to `request.max_tokens`: `low/medium/high/xhigh = 25/50/75/90 %`, floored at 1024 tokens, ceilinged at `max_tokens - 1`. `none` / `minimal` skip the thinking block. Only applied when the model declares `supports_thinking`. |
| DeepSeek | Forward as `reasoning_effort` on V4-pro-class; no-op on `deepseek-reasoner` (always thinks). |
| OpenRouter | Best-effort passthrough; the routed upstream interprets. |

Anthropic chose **proportional over fixed budgets** because:
- A 200K-context model getting `"high"` effort shouldn't be capped
  at the 16384 a fixed map would impose.
- Fixed budgets need re-tuning every time a model bumps its context.
- Proportional decouples the knob from model-class assumptions.

The trade-off: identical `reasoning_effort="high"` calls produce
different absolute thinking budgets across requests with different
`max_tokens`. We accept that — `max_tokens` is itself the user's
"how much output am I willing to pay for" knob, and proportional
keeps the *fraction of output spent reasoning* stable.

## Consequences

- Callers in cairn (orchestrator, delegation tools, memory
  extraction) pass a single semantic value; providers translate.
- A future model family with neither an enum nor a budget knob
  ignores the field — no schema break.
- The `xhigh` literal is OpenAI-specific (their newest tier);
  Anthropic still resolves it (90% of `max_tokens`); DeepSeek
  passes through (server may 400 if not supported by the SKU).

## Alternatives considered

- **Per-provider enum**: forces every caller to encode
  provider-specific values, defeating the abstraction. Rejected.
- **Pass through the raw vendor type**: leaks provider details into
  the orchestrator. Rejected.
- **Fixed Anthropic budgets** (1024 / 4096 / 16384 / 32768):
  intuitive but doesn't adapt to context-window growth. Rejected
  in favour of proportional.

## Implementation

Adapter helpers:
- `cairn.providers._anthropic._resolve_thinking_budget`
- `cairn.providers._openai._is_reasoning_model` gates forwarding
- `cairn.providers._deepseek` (V4-pro-class detection deferred —
  current models served are `deepseek-chat` / `deepseek-reasoner`)
