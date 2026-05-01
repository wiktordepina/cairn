# 0049 — Memory slash/tool symmetry; deferral of curation UI

**Date:** 2026-04-28
**Status:** accepted

## Context

V1 memory (shipped at 0.7.0, ADR 0021–0024) wired up tier-1
observations: a post-turn extractor writes facts/preferences/
insights/etc. into `memory_entries`, and a pre-turn preparer
calls `MemoryService.retrieve()` to surface the top-k for the
current user message. Both ends ran *automatically*; neither end
gave the user — or the LLM — a way to ask "what do you remember?"
or to assert "remember this" outside the extraction flow.

After ~10 weeks of dogfooding the auto-retrieve, three failure
modes recurred:

1. The user's first turn doesn't lexically mention the relevant
   prior context, so the auto-retrieve misses it. Later turns
   surface the gap ("oh, you've told me before that…").
2. A delegation child runs against a partially-shared
   `memory_space` and benefits from a targeted lookup that the
   parent's top-k didn't cover.
3. The user wants to bookmark something *now* — a decision, a
   preference clarification — without waiting for the post-turn
   extractor to (maybe) catch it via heuristics.

V2's tier-3 curation UI (ADR 0024) addresses (3) by promotion: a
proposer suggests new `memories/<slug>.md` files, the user
accepts, MEMORY.md gets re-indexed. That's a substantial brick;
shipping it inside V1 isn't realistic.

## Decision

Ship three surfaces in 0.19.0, scoped tightly:

1. **`/recall <query>`** — top-k FTS5 search of the active
   session's `memory_space`, rendered in a modal. Read-only;
   results are user-only and never appended to the conversation.
2. **`/remember <text>`** — explicit save of a fact memory,
   bypassing the post-turn observation queue. Confirmed via
   toast. Dedup-on-store applies (same threshold as the
   extractor); a near-identical input refreshes the existing
   row's `updated_at` rather than creating a duplicate.
3. **`recall` built-in tool** — symmetric with `/recall`. Tier-0,
   read-only, no approval gate. Reuses the same
   `MemoryService.retrieve()` underneath, so the LLM sees the
   same scoring the pre-turn preparer would.

Three deliberate non-features:

- **No `/memory` browser.** A full-screen list view with per-row
  expand is meaningful only once curation lands (you'd want to
  edit, archive, demote). V1 stays tight.
- **No archive / soft-delete column.** No schema migration. The
  V1 escape hatch for unwanted memories is hand-editing
  `<data_dir>/cairn.db`.
- **No inline `--type` / `--importance` flags on `/remember`.**
  Every `/remember` writes a `fact` with the configured
  importance (default 7, one notch above the extractor default
  of 5; configurable per profile via
  `[memory] explicit_remember_importance`). Adding flags is
  friction without demand; revisit post-1.0.0.

## Why slash output stays user-only

A `/recall` that fed its hits back into the conversation would be
indistinguishable, from the model's perspective, from the
existing pre-turn auto-retrieve. We already have a channel for
that — and giving the user a *separate* button that does the same
thing is a UX trap (users would assume one of them must be
"better"). Keeping `/recall` user-only preserves a clean
distinction: slash commands are introspection for the human
operator; the matching `recall` tool is the LLM's channel.

The two paths share one code path
(`MemoryService.retrieve_scored` for the modal, `retrieve` for
the tool). They're guaranteed to surface identical hits for
identical inputs.

## Why `recall` is tier-0 (no approval gate)

Read-only against the user's own memory; reveals nothing the
auto-retrieve preparer wouldn't reveal anyway. Same posture as
`now` and `file_read`. Approval prompts on every recall would
turn into confirmation fatigue.

## Why importance default is 7, configurable

Explicit user intent ("remember this") is a stronger signal than
auto-extraction (the extractor default is 5). Bumping default to
7 nudges retrieval to favour `/remember`-saved facts on close
ranking decisions without dominating the score (importance
contributes 30% of the composite). Per-profile override gives
deployments room to tune without recompiling.

## Why no curation UI in V1

Tier-3 curation is its own brick — proposer, MEMORY.md
re-indexer, accept/reject UX, conflict resolution against
existing tier-3 entries. It's V2 per ADR 0024 and stays there.
The 0.19.0 brick deliberately ships only the surfaces that
*don't* require those collaborators to exist.

## Consequences

- The `/recall` modal exposes the composite score column. Score
  visibility parallels `/cost`'s exposure of dollar figures —
  transparent over hidden. If users complain post-1.0.0 we hide
  it behind a debug flag.
- Tool toggling is *not* added to `ToolsConfig` for `recall`
  alone. If a deployment ever needs to disable `recall`, that
  wants to be uniform across all built-ins (a separate brick
  adding a per-tool `enabled` knob), not a one-off field.
- `MemoryService.retrieve()` gains a `truncate_content` kwarg.
  The pre-turn preparer keeps the default (truncate-on); the
  modal and tool pass `False` to receive full bodies. Backwards
  compatible — existing callers see no change.
- A new `MemoryService.retrieve_scored()` method returns
  `(score, MemoryEntry)` pairs for the modal's score column.
  Same scoring underneath; just exposes the score that
  `retrieve()` discards.

## Alternatives considered

- **Routing `/recall` output back into the conversation as a
  user message.** Rejected: see "Why slash output stays
  user-only".
- **Skipping the `recall` tool, slash commands only.** Rejected:
  the auto-retrieve covers the easy cases, but the three failure
  modes in §Context all require the model to make an on-demand
  query. Slash commands can't help the model.
- **Adding a `/memory` browser as a stub for V2 curation.**
  Rejected: a list view without curation actions is dead weight;
  the keyboard real estate (`m` is short and easy to type) is
  worth keeping for the actual browser.

## Related

- ADR 0021 — Memory tier-1 storage shape.
- ADR 0024 — Tier-3 curation deferred to V2.
- ADR 0042 — Surgical reload boundary (defines what
  `/reload` does and doesn't swap; `recall` tool is registered
  on the orchestrator and persists across reloads).
- ADR 0048 — Cross-model swap resilience (unrelated; ADR 0048 is
  the most recent prior, hence this one is 0049).
