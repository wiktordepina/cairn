# 0021 — Ship tier-1 memory first, defer reflection and curation UI

**Date:** 2026-04-22
**Status:** accepted

## Context

Architecture doc §4.11 describes three memory tiers:

- Tier 1 — raw observations, extracted automatically post-turn.
- Tier 2 — reflections, synthesised from unreflected observations.
- Tier 3 — MEMORY.md, curated high-confidence facts plus (in this
  repo's variant) per-memory bodies under `memories/`.

Each tier has a write path (how entries are produced) and a read path
(how entries re-enter the companion's context). Shipping all three
write paths in V1 would stretch the memory brick across a reflection
worker, a curation UI, an approval flow, and the retrieval surface —
weeks of work before the companion remembers anything at all.

## Decision

**V1 ships tier 1 end-to-end plus the *read* side of tier 3**:

- Tier 1 — `MemoryRepo`, `ObservationLog`, `Extractor`,
  `ObservationExtractionQueue`, `MemoryService` retrieval.
- Tier 3 — `StandardContextManager` reads MEMORY.md and loads it into
  every companion prompt as an index.

**V1 does NOT ship**:

- The tier-2 reflection pipeline (watermark, novelty gate, batching,
  MEMORY.md auto-proposal with approval UI).
- A write path for tier 3 — curated memories are hand-edited in V1.
  The `/remember` slash command that automates this lands with the UI
  brick.

## Consequences

- **Positive.** The companion remembers things from the first session
  it runs. Retrieval surfaces tier-1 observations without a
  reflection loop needing to decide what's worth keeping. MEMORY.md
  and soul/user-context docs still flow into context, so curated
  facts and identity are visible.
- **Positive.** Tier 2 landing later is a pure addition — the tier-1
  watermark column (`reflection_watermarks`) is the only new
  persistence surface it needs.
- **Negative.** Dedup across sessions relies on `SequenceMatcher`
  only; true semantic near-duplicates ("User loves keto" vs "User is
  on a ketogenic diet") will produce two rows. [ADR 0022](0022-memory-dedup-threshold.md)
  addresses the trade-off.
- **Negative.** Without tier 2, curated MEMORY.md entries only change
  when the user edits the file by hand. Some users won't curate;
  their MEMORY.md will stay near-empty until V2.

## Alternatives considered

**Ship tier 1 + tier 2 in V1, defer tier 3 write-side only.** Tier 2
needs a prompt, a worker, an approval surface, and a novelty gate —
more scope than fits this release. Deferring keeps the V1 surface
small and lets real usage inform the reflection prompt before we
commit.

**Skip tier 1, ship only MEMORY.md.** Would mean the companion only
remembers what the user manually curates. That's the shape of the
mait-code "curated-only" mode, and it lost: people don't curate, so
the companion doesn't remember, so the curation premise collapses.
Tier 1 is the layer that makes the companion feel present.
