# 0025 — Dedicated `ModelRole.EXTRACTION` with a first-class local-model path

**Date:** 2026-04-22
**Status:** accepted
**Refines:** [ADR 0006](0006-role-based-model-selection.md) (adds a
new role; does not change the resolution mechanism).

## Context

The architecture doc describes the memory extractor as "the utility
model" — implicitly sharing `ModelRole.UTILITY` with titling and
any other cheap-call workload. In practice these have different
needs:

- Extraction runs 10–100× per day, needs structured JSON output,
  tolerates a weaker reasoner, and benefits from a pinned schema.
- Titling runs once per session, returns a short free-text string,
  and fails gracefully if skipped.
- Compaction (V2) runs rarely, needs long-context handling.

Sharing `UTILITY` would force one model to satisfy all of them. And
once a user pins `UTILITY` to (say) Claude Haiku for titling, they
can't cheaply swap extraction to a local Qwen without dragging
titling along with it.

The `OpenAIProvider` already supports OpenAI-compatible local
servers via `base_url`, so the runtime cost of a local extraction
model is near zero — what's missing is a config surface for
pointing at one.

## Decision

**Add `ModelRole.EXTRACTION` alongside `PRIMARY`, `UTILITY`,
`REASONING`, `CODING`, `VISION`, `FAST`.**

`Extractor.resolve_model()` looks up `EXTRACTION` first. If no model
carries that role, it falls back to `UTILITY` — so profiles that
haven't opted in to a dedicated extractor keep working unchanged.

**Document the local-model path as first-class.** The memory docs
page includes a recipe for wiring llama.cpp / vLLM / LM Studio via
`base_url` and the JSON schema exposed by
`ExtractionResponse.model_json_schema()`. Recommended local models
(as of 2026-04): Qwen 2.5 7B Instruct (Q4_K_M, ~5 GB VRAM), Llama
3.1 8B Instruct, Phi-3.5 mini (smaller, weaker).

**Usage is recorded with `role = "extraction"`** so cost
aggregations can separate extraction spend from primary spend.

## Consequences

- **Positive.** Profiles can pin extraction to a free local model
  without touching titling or primary.
- **Positive.** Cost attribution is clean — `UsageRepo` rows tagged
  `operation = extraction` are already present; this ADR pins
  `role = extraction` alongside them.
- **Positive.** When V2 adds separate roles for compaction or
  reflection, this pattern is already established — new ModelRole
  member + Extractor-style `try/fallback` resolve.
- **Negative.** One more enum member to document. One more
  role-claim to resolve. Not free, but cheap.

## Alternatives considered

**Stay on `UTILITY` and document model choice as profile-wide.**
Simpler but forces the "one model for cheap stuff" coupling.

**Ship a new provider adapter specifically for local models.**
Overkill — the OpenAI adapter already speaks OpenAI-compatible local
servers. A new adapter would be a duplicate.

**Bundle a small extraction model and run it in-process.** Binds
cairn to a specific model weights distribution + a runtime like
`llama-cpp-python`. Not worth the install-footprint increase;
users who want local extraction already have a local server.
