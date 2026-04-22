# 0026 — Per-turn extraction with N-turn context window and length gate

**Date:** 2026-04-22
**Status:** accepted

## Context

The architecture doc says: "after each completed companion turn,
asynchronously. Queue-based, uses the utility model. The
Orchestrator does not wait." That is the shape shipped in V1.

During design review the alternative — *per-session* extraction at
session close — was raised. Session-level extraction is cheaper
(one utility call per session instead of per-turn) and less noisy
(short back-and-forths don't individually trigger calls).

The trade-offs were analysed and per-turn won, but with two
additions that address the raised concerns.

## Decision

**Extract per-turn, but:**

1. **Include the previous `extraction_context_turns` turns (default
   3) as disambiguation context** alongside the latest turn in the
   prompt. Context turns are marked `[context]`; the target turn is
   marked `[LATEST]`. Observations are extracted from the LATEST
   portion only; prior turns exist so the model can resolve
   references like "do that" or "like we discussed".
2. **Apply a length gate before paying for the call.** If the
   latest turn's combined user + assistant text is below
   `MemoryConfig.min_extraction_chars` (default 200), skip
   extraction entirely. Avoids burning utility-model calls on
   "thanks"/"ok"/"yes" turns where there's nothing to observe.

**Cost control levers stacked:**

- Length gate skips cheap-noise turns.
- `max_extraction_cost_usd = 0.01` per-call cap truncates
  extractions that run away mid-stream.
- `max_pending_extractions = 100` drops oldest queued jobs if the
  queue saturates — better to miss old observations than to OOM.
- Dedup collapses repeated observations at store time, so even
  noisy per-turn extraction shouldn't grow `memory_entries`
  unboundedly.

## Consequences

- **Positive.** Observations from a turn are retrievable in the very
  next turn. Per-session extraction means nothing from an ongoing
  session is retrievable until it closes — painful for long
  working sessions.
- **Positive.** Session-resume is trivial — extraction is
  incremental, so resuming a session is just "keep extracting per
  turn". Per-session extraction would need to track "did we already
  extract this close-event" on resume.
- **Positive.** Crash mid-session loses at most one turn's worth of
  extractions, not the entire session's.
- **Negative.** Higher call count. At ~$0.005 per Haiku extraction
  and 50–100 extracted turns/day, this is ~$9–18/month — bounded,
  and the local-model path ([ADR 0025](0025-extraction-role-and-local-models.md))
  brings it to zero for self-hosted users.
- **Negative.** Turn-level prompts lack session-level summarisation
  — the extractor sees 3 prior turns, not "the arc of the
  conversation". V2 reflection fills this gap by synthesising
  across observations after the fact.

## Alternatives considered

**Per-session extraction at archive time.** Cheaper, cleaner single
prompt, but breaks next-turn retrieval and complicates resume.

**Per-turn with the full session transcript each time.** Would
balloon input tokens on long sessions and duplicate extraction work
across overlapping windows — the dedup path would absorb the damage
but we'd pay for the tokens anyway.

**Per-turn with no context at all.** Tried first. Short turns like
"yes, do that" produce empty observations because the extractor
doesn't know what "that" refers to. Adding 3 prior turns as context
was the smallest addition that fixed it.
