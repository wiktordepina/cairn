# 0028 — Turn-block granularity for truncation

**Date:** 2026-04-22
**Status:** accepted

## Context

Truncation (ADR 0027) needs to pick a boundary between messages
that should stay and messages that should go. The naive choice
— drop the first N messages — breaks two invariants that every
provider we ship assumes:

1. **Tool pairing.** An assistant `ToolUseBlock` must be followed
   by a user `ToolResultBlock` with the same `tool_use_id`.
   Drop the user message that carries the result while keeping
   the assistant's tool call and Anthropic rejects the request;
   OpenAI is slightly more forgiving but behaves unpredictably.
2. **First-message role.** Anthropic requires the first message
   in `messages` to be `role=user` with non-tool-result content.

A per-provider fix (scan each request, fix up pairs
provider-side) is fragile: the set of invariants changes as
providers evolve, and the fix-up logic has to live in every
translator.

## Decision

**Compaction drops whole "turn blocks" from the front of the
message list.** A turn block is a contiguous range of messages
that starts at a user message whose content is *not* purely
tool results. Every message belongs to exactly one block.

The `iter_turn_blocks` helper in `cairn.compaction` walks the
message list left-to-right, opens a new block whenever it sees a
user message with real content (i.e. not purely
`ToolResultBlock`s), and yields the block bounds.
`TruncatingCompactor` drops blocks whole; it never slices within
a block.

## Consequences

- **Positive.** Tool-pair integrity is automatic — a tool call
  and its result always belong to the same block, so dropping
  the block drops them together.
- **Positive.** The first surviving message is always a user
  message with real content — exactly what Anthropic requires.
- **Positive.** No per-provider logic. The translator in each
  provider adapter stays unchanged.
- **Negative.** Block granularity wastes a small amount of
  budget: at the moment compaction decides it fits, the last
  dropped block might have been only ~20% over budget.
  Acceptable — the `safety_margin_tokens` knob already accounts
  for worse imprecision than this, and the alternative is the
  per-provider fragility above.
- **Negative.** A pathological single-block conversation (one
  user message followed by dozens of tool-call rounds) can't be
  trimmed at all. In practice this is rare; when it happens, the
  overflow advisory from ADR 0029 surfaces the problem to the
  user.

## Alternatives considered

**Per-message truncation with a post-pass that re-pairs orphaned
tool calls.** Rejected — it needs to synthesise plausible
`ToolResultBlock` content, which requires either lying to the
model ("tool call failed") or running the tool again. Both are
worse than dropping a whole block.

**Provider-specific block shapes.** Rejected as premature —
every provider we ship today respects the same tool-pair
invariant, and changing strategy per provider would force the
compactor to know which provider it's compacting for. That
couples two layers that are currently independent.
