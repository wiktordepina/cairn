# 0011 — `turns` table with an explicit state machine

**Date:** 2026-04-20
**Status:** accepted

## Context

A turn passes through many internal phases: user-message persistence,
memory retrieval, iteration loop, tool dispatch, finalisation. Each
phase can fail, and the process can crash at any point.

Options for tracking a turn's life:

1. **Implicit.** No dedicated state; infer progress from messages +
   tool_calls + usage rows. Hard to distinguish a "quiet but running"
   turn from one that crashed mid-stream.
2. **An explicit `state` column on a `turns` table.** One row per
   turn. Conditional `UPDATE` statements drive transitions. Crash
   recovery becomes "find rows in non-terminal states".
3. **Event sourcing.** Append-only log of transitions. Replayable but
   heavy.

We already decided (ADR 0008) to use a state column rather than event
sourcing for tool calls, for the same reasons. Applying the same
reasoning to turns:

- "What state is this turn in right now?" is a hot-path query; one
  row is trivially faster than reconstructing from events.
- "Did the process crash mid-turn?" is a startup-path query; an index
  on `state NOT IN (terminal)` makes it a single scan.
- Structured log lines capture the transition history, so audit trail
  isn't lost even though the DB stores the latest state.

## Decision

Add a `turns` table to the schema. Columns include
`state TEXT NOT NULL`, `iteration_count`, `model`, timestamps, and
terminal-state fields (`aborted_reason`, `stop_reason`).

The state machine, named explicitly in `TurnState`:

```
STARTED → MEMORY_RETRIEVAL → ITERATION ↻ (TOOL_DISPATCH)
                               ↓
                           FINALISING
                               ↓
                       EXTRACTION_ENQUEUED (optional)
                               ↓
                           COMPLETED

   (any state → ABORTED on cancel / crash / unrecoverable error)
```

Every transition is a conditional `UPDATE`: `SET state = :new WHERE id
= :id AND state = :expected`. If zero rows affected, the code is in an
illegal state — the repository raises `InvalidTurnTransition`.

`resume_aborted_turns()` runs on application boot: scans non-terminal
rows, marks them `ABORTED` with `aborted_reason='process_crash'`.

`turn_id` threads through `messages.turn_id`, `tool_calls.turn_id`,
and `model_usage.turn_id` so "everything produced during this turn"
is one query.

## Consequences

**Easier:**

- At 2am, "what state is this turn in?" is a single row.
- Crash recovery is one SQL query.
- `cost_for_turn(turn_id)` aggregations are cheap.
- Adding a new state is additive — an enum value + a handful of
  transitions.

**Harder:**

- Illegal transitions surface as `InvalidTurnTransition` rather than
  silently corrupting state. That's by design but means the turn-loop
  code must be careful. Tests cover the critical paths.
- The DB schema grew a table and three columns. Migration 0002
  carries the weight.
- Conditional UPDATEs serialise transitions — there's no "same state
  twice" idempotency. For V1 this is correct; if future work needs to
  retry transitions, we'll add a safer pattern.

**Ongoing cost:**

- The orchestrator is responsible for the state machine. No other
  code path writes to `turns`.
