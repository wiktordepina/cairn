# 0008 — Tool-call state as a column, not an event stream

**Date:** 2026-04-20
**Status:** accepted

## Context

A tool invocation walks a state machine:

```
pending → approved → executing → completed | failed | timed_out | cancelled
  └──── rejected
```

Two representations are natural:

1. **One row per tool call, `state` column updated in place.** The row
   carries the current state; transitions are `UPDATE` statements.
2. **One row per state transition (event-sourced).** An append-only
   stream of events; current state is the latest event for that call.

Event sourcing has real advantages: full audit trail, easy replay,
straightforward "when did this become approved?" queries, natural fit
for append-only storage. It also has real costs: more writes per call,
more rows to query, more complex "what is the current state?" code.

For cairn's use case the interesting questions are almost always:

- Current state of a call (for the UI and for approval logic).
- Duration from start to completion.
- Error class on failure.
- Full lifecycle history for one call when debugging.

The first three are one-row queries. Only the fourth benefits from
a full event trail.

A transition history is useful, but structured log entries (which we
already emit at every transition via `structlog`) cover that need
without burdening the hot path's writes.

## Decision

One row per tool call in `tool_calls`. State transitions are
conditional `UPDATE` statements:

```sql
UPDATE tool_calls
SET status = 'approved', approved_at = :now, approved_by = :by
WHERE id = :id AND status = 'pending';
```

An `UPDATE` that affects zero rows means the state machine was in the
wrong state; the repository raises `InvalidToolCallTransition` so the
bug isn't silent.

Structured log entries (`tool.pending`, `tool.approved`, …) carry the
full transition history. Log rotation retains them for a configurable
window; the DB retains the final state forever (until the session is
deleted).

## Consequences

**Easier:**

- "What's the current state?" is `SELECT status FROM tool_calls WHERE
  id = …`. One row, one column, fast.
- Duration tracking is a `completed_at - started_at` on the same row.
- Approval UI queries "pending approval" as `SELECT … WHERE status =
  'pending'`.
- No ambiguity about "what if two transitions happen concurrently?" —
  the conditional `UPDATE` serialises them; one wins, one raises.

**Harder:**

- No built-in audit trail in the DB. If the log files rotate out, the
  transition history is lost for that call. For cairn's blast radius
  (single user, one tool call at a time in practice), this is
  acceptable.
- Future analysis like "distribution of times from approved to
  executing" needs either log parsing or a dedicated events table.
  That's a V3+ concern; we can add an events table *in addition to*
  the state column if ever needed, without changing the existing
  semantics.

**Ongoing cost:**

- Every state change writes to the log *and* updates the DB row. The
  two paths must agree; a transition that updates one without the
  other is a bug. The repository layer is the single funnel — if you
  update state, you go through the repo, and the repo handles both.
