# 0027 — Truncation before LLM summarisation for compaction

**Date:** 2026-04-22
**Status:** accepted

## Context

Every LLM conversation eventually outgrows the model's context
window. Cairn needs a compaction strategy. The arch doc §8
earmarks "LLM-driven compaction (summarise the oldest span with
the utility model)" for V2 and "basic compaction (truncation with
preserved last N turns)" for V1.

This ADR pins that ordering.

## Decision

**V1 compaction drops oldest turn blocks verbatim.** No LLM calls,
no summaries, no clever condensation. A `TruncatingCompactor`
implementing the `MessagePreparer` protocol sits in the
orchestrator's prepare chain, counts tokens via the provider's
native tokenizer, and drops whole turn blocks from the front of
the message list until the request fits a configurable budget.

**Defaults shipped:**

- `preserve_last_n_turns = 6` — hard floor on surviving turn
  blocks.
- `safety_margin_tokens = 2048` — held back from
  `context_window` on top of the reserved output tokens.
- `min_history_tokens = 1024` — if the effective budget falls
  below this, the profile is misconfigured; log ERROR and pass
  the request through unchanged.

## Consequences

- **Positive.** No LLM call on the critical path of every
  over-budget turn. Compaction adds at most a few hundred
  milliseconds of `count_tokens` RTT on Anthropic (free; not
  billed as input) and microseconds elsewhere.
- **Positive.** Deterministic. No summarisation failure modes
  (hallucinated history, silent drift, shape changes across
  model versions).
- **Positive.** Simple to audit. A `HistoryCompacted` event
  records exactly how many blocks and tokens were dropped.
- **Negative.** Dropped turns are gone from the conversation as
  the model sees it. Mitigated by the memory brick: long-lived
  facts persist in `memory_entries` and are retrieved per-turn
  back into the system prompt, so useful information survives
  compaction in a different lane.
- **Negative.** In a very long tool-heavy run the floor may be
  hit and the turn continues without any older context. ADR
  0029 covers how that case is surfaced rather than silently
  swallowed.

## Alternatives considered

**LLM-driven summarisation in V1.** Rejected for V1 because
summarisation has failure modes (lossy, slow, expensive,
non-deterministic) that we don't want to ship before we have
telemetry about real conversation shapes. V2 revisits once the
truncation baseline and memory brick are proven.

**Sliding-window truncation that cuts mid-turn.** Rejected
because it risks orphaning a `ToolUseBlock` from its matching
`ToolResultBlock`, which Anthropic rejects outright. ADR 0028
documents the turn-block granularity that avoids this.

**Per-segment hard caps** (e.g. "retrieved_memories ≤ 2 KB, tools
≤ 3 KB"). Rejected because segment budgets are already controlled
by their own knobs (`retrieval_k`, tool catalogue size, etc.);
adding a second budget knob on top of those would mostly just
surprise users.
