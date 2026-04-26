# 0048 — Cross-model swap resilience

**Date:** 2026-04-26
**Status:** accepted

## Context

[ADR 0045](0045-model-swap-semantics.md) fixed mid-session model
swap as a first-class operation: the user picks a different model
in `/model`, the session row's `model` column updates, the next
turn streams under the new model, and the conversation history
goes along for the ride. The semantics were sound; the
*compatibility* edges weren't.

Once the picker landed, real swaps surfaced two pre-existing
assumptions that don't hold once a non-reasoning model
participates in a thinking-mode conversation:

1. **DeepSeek thinking SKUs require `reasoning_content` on every
   prior assistant message.** The provider rejects requests with
   `"reasoning_content in the thinking mode must be passed back
   to the API"` whenever a single assistant row is missing the
   field. In a homogeneous DeepSeek conversation that's invisible
   — every assistant turn carries a `ThinkingBlock`. Swap to
   Kimi (via OpenRouter) and back, and the kimi-produced
   assistant messages have no thinking content; the next
   DeepSeek thinking turn is rejected.

2. **Tool-call ids are not globally unique across providers.**
   Anthropic (`toolu_*`) and DeepSeek (`call_*`) issue
   conversation-unique random ids; Kimi via OpenRouter emits
   positional names like `functions.web_fetch:0` that repeat on
   every turn and across sessions. `tool_calls.id` is a primary
   key, so the second insertion fails
   `UNIQUE constraint failed: tool_calls.id` and aborts the
   turn.

Both were latent — pure single-provider sessions never tripped
them — and both surfaced the moment 0.18.0's `/model` picker met
the OpenRouter side of the model registry.

## Decision

Fix at the boundaries that *own* each constraint, not in
storage or in cross-cutting middleware.

### `reasoning_content` on every DeepSeek assistant row

`cairn.providers._deepseek.format_messages` now emits
`reasoning_content` on every assistant row. When the message has
a leading `ThinkingBlock`, the field carries `block.thinking`;
otherwise it's the empty string.

The earlier docstring's "required only when the prior turn had
tool calls, optional otherwise" claim was an artefact of
homogeneous-DeepSeek testing — every assistant turn happened to
have a `ThinkingBlock`, so the "optional" branch was never
exercised against the API. Empty string satisfies the API's
"must be passed back" check and is invisible to the model.

### Globally unique tool-call ids

The orchestrator namespaces every provider-issued tool-call id
with the assistant message UUID at the boundary
(`<message_id>:<provider_tool_call_id>`). Persisted ids are then
unique across turns and sessions even when the upstream
provider reuses positional names.

The model treats tool-call ids as opaque correlation keys
between `assistant.tool_calls[i].id` and the matching
`tool_result.tool_call_id` *within the same conversation*; the
substituted id round-trips both sides identically, so the
provider sees a consistent pair. No schema change, no FK
churn, no per-provider special-casing.

## Consequences

- **No schema migration.** The `tool_calls.id` PRIMARY KEY
  stays put; `model_usage` and message-content shapes are
  unchanged.
- **Cross-model swap sequences work end-to-end.** Tested on the
  `pro → kimi-k2.6 → flash` path that originally surfaced both
  bugs.
- **`reasoning_content` is now sent unconditionally to
  DeepSeek thinking models.** The empty-string branch carries
  no information, but the API treats it the same as the
  homogeneous case once the field is present.
- **Persisted tool-call ids no longer match the provider's
  outgoing wire format.** Anyone tailing logs sees
  `<uuid>:<provider-id>` rather than the bare provider id;
  structured event payloads (`tool_call_planned` etc.) carry
  the namespaced form. The provider id is recoverable as
  everything after the first `:`.
- **A second namespacing pass would double-prefix.** The
  orchestrator's rewrite is idempotent within a turn (Start /
  Delta / End all see the same provider id and produce the
  same safe id) but would compound if some future code path
  re-fed a persisted ToolUseBlock through the same boundary.
  The current rewrite happens once per stream, at the
  provider-event seam, and that's the only call site.

## Alternatives considered

### Schema relaxation for `tool_calls.id`

Make the primary key composite (`(session_id, id)` or
`(turn_id, id)`). Cleaner storage shape, but cascades into
`tool_call_results.tool_call_id` (a foreign key referencing
`tool_calls.id`) and every read path through
`_tool_calls_repo`. The orchestrator-side rewrite is invisible
to the rest of the codebase and required no migrations.

### A non-empty `reasoning_content` placeholder

Send something like `"(no recorded reasoning)"` to make the
field obviously synthetic. Empty string was preferred because
the API accepts it and the model never sees the trace anyway.
A non-empty placeholder would either need translation per
language or risk being mistaken for the model's own reasoning.

### Per-provider message-rewrite middleware

Define a `MessageTransformer` that strips or annotates
cross-model artefacts before each provider call. Overkill for
two narrow constraints, and pushed knowledge of the
constraints out of the providers that actually own them.
