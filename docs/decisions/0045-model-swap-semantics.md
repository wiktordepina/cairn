# 0045 — `/model` mid-session swap semantics

**Date:** 2026-04-26
**Status:** accepted

## Context

`/model` shipped in 0.10.0 as a read-only status line: it printed
the resolved primary / utility model ids for the active session.
ADR 0042 reserved the *write* form — a real swap — but explicitly
deferred it because cross-provider history (tool-call id schemes,
thinking-block formats, cache markers) is fragile, and at the
time the safe path was "session stays on its launch model;
restart cairn to switch".

Two things changed that made the swap worth doing:

1. The auto-compactor wiring fix (this brick) means the model
   sees a budget-trimmed history, so a swap to a model with a
   smaller context window now degrades gracefully.
2. Operators kept asking for a way to flip models without
   killing their session — the "restart to switch" rule was the
   most-cited UX paper-cut from V1.

Open design questions:

- What happens to the existing transcript on a swap?
- How does `/model` interact with `/reload` — config is the
  source of truth, but a runtime swap rewrites a column the
  config implicitly owns.
- Where does the swap state live — a new column, a new layer,
  the session row?

`/archive` (also new in this brick) raised a parallel question
about UX disclosure: V1 has no session picker, so `/archive`
implies the app exits.

## Decision

### `/model` opens a picker over `[models.*]`

No tail-shortcut form. The picker shows every model declared in
config, marks the session's current model with a `◀ current`
suffix (and a header line `current: <id>`), and surfaces role
tags (`[primary]`, `[utility]`) for orientation. Selecting the
current model is a no-op. Esc aborts.

### Three-way choice on in-progress sessions

When the picker selects a *different* model:

- **Fresh session** (no persisted messages): apply the swap
  immediately, no prompt.
- **In-progress session** (≥1 persisted message): present a
  three-option modal:

  | Choice | Action |
  |---|---|
  | `k` keep history | Same session row, `session.model` updated, transcript preserved. Next turn streams under the new model. |
  | `s` start fresh | Archive current session, open a new one of the same type / persona on the picked model. |
  | `a` abort | No change. |

**No "compact and inject" branch.** V1 compaction is request-time
view-only (per `compaction-brick-design.md` §1) — there is no
persistent compacted summary to inject as seed history. The
auto-compactor will trim the outbound request on the next turn
to fit the new model's context window, which covers the common
motivation for a compact-then-inject branch (model with smaller
context). When V2 LLM-driven compaction lands and there's a real
persistent summary to copy, this can be reconsidered.

### `/reload` reverts to config

Config is the single source of truth. When `/reload` detects
that the session's current model differs from what the profile's
`primary_model` ref now resolves to, it actively rewrites
`session.model` to the config-resolved value (mode `"revert"`).
The drift banner becomes informational ("session model reverted
to config — was X, now Y") rather than a "restart to switch"
hint. This means a `/model` swap *disappears* on the next
`/reload` — that's load-bearing: a runtime override should not
silently outlive a config change.

### Persistence: column on `sessions`

`session.model` already exists. The swap rewrites it via a new
`SessionRepository.update_model` method. No new schema or layer.
The orchestrator's `swap_session_model(id, new_id, mode=…)` is
the single entry point and emits a `ModelSwapped` UIEvent for
observers. Modes are `"keep"`, `"fresh"`, `"revert"`.

### `/archive` informs and confirms before quitting

V1 has no session picker. Once the active session is archived,
the app has nowhere to go. `/archive` therefore:

1. Opens a `ChoiceModal` warning that archive will close the
   app ("V1 has no session picker yet, so there is nowhere to
   go after the archive").
2. On confirm, archives + exits.
3. On cancel / Esc, no-ops.

The inform-up-front + explicit-confirm pattern avoids the "I
just wanted to archive, why did the app close?" surprise. Once
a session picker exists (V2), this can drop the quit and the
confirm step.

## Consequences

- `Session` rows now carry a column the user can rewrite at
  runtime. `/reload` keeps that drift bounded.
- Cross-provider model swaps work because cairn already
  normalises history into provider-neutral content blocks (per
  the providers brick) — the new model re-translates on its
  next turn.
- Prompt cache invalidates on swap. Cache markers are
  model-scoped already; the auto-compactor accepts the cache
  miss as the cost of the user's explicit choice.
- A `ChoiceModal` + `ModelPickerModal` pair lands as part of
  this brick. Both are reusable: `/archive` reuses `ChoiceModal`
  for its confirm step.

## Alternatives considered

- **Tail-form `/model <id>` with no picker**: forces the user
  to know exact model ids. Rejected — the picker is the same
  shape as `/conventions` / `/tools` in surface complexity.
- **Compact-and-inject as the in-progress default**: ruled out
  by V1's no-persistent-compaction baseline (§ above).
- **Don't revert on `/reload`**: leaves a runtime override
  outliving its trigger. Worse: user edits config to flip the
  primary model role and the active session ignores it. Defeats
  the "config is the source of truth" rule.
- **`/archive` archives silently and stays open with a stub
  screen**: feasible but requires inventing a stub UX with no
  obvious next step. Not worth designing for V1.
