# 0012 — No foreign-key constraint on `turn_id` columns

**Date:** 2026-04-20
**Status:** accepted

## Context

Migration 0002 introduced a `turns` table and added `turn_id` columns
to `messages`, `tool_calls`, and `model_usage`. The natural design
question: should `turn_id` be a SQL foreign key to `turns(id)`?

- **Pro FK.** Enforces "a row tagged with a `turn_id` points at a real
  turn". Safety in depth; the DB wouldn't accept inconsistent writes
  even if a caller mis-threaded the ID.

- **Con FK.** There's a circular write dependency.
  `turns.user_message_id` references `messages(id)` (a turn *is* a
  turn over a user message), so the user message must exist before
  the turns row. Equally, `messages.turn_id` referencing `turns(id)`
  means the turn must exist before the message. Both can't hold at
  once under SQLite's FK enforcement.

Two escape routes:

1. **Persist the user message first with `turn_id = NULL`, then
   INSERT the turn, then UPDATE the message's turn_id.** Adds a write
   + complicates crash-atomicity (message exists briefly without the
   turn_id).
2. **Drop the FK on the added columns.** `turn_id` is still written
   to every row during an orchestrator turn; integrity is enforced
   by the orchestrator, not by the DB.

## Decision

Drop the FK. `turn_id` columns are plain `TEXT`, indexed but not
referenced.

The `turns` table retains its outgoing FKs (`session_id`,
`user_message_id`) — those writes happen after the referenced rows
exist.

Documented explicitly in the migration SQL so nobody "helpfully"
adds the FK back later.

## Consequences

**Easier:**

- Write ordering is the natural one: user message first, then turns
  row. No placeholder updates, no dangling-state windows.
- One fewer migration step (no ALTER to promote to FK later).

**Harder:**

- A bug in the orchestrator that writes the wrong `turn_id` won't be
  caught by the DB. Mitigation: the orchestrator is the only code
  path that writes `turn_id`; the turn_id is generated once per
  turn and threaded through. Tests assert that persisted rows have
  the expected turn_id.

**Ongoing cost:**

- Tooling that joins these tables to `turns` will occasionally
  encounter NULL `turn_id` (for old rows predating the feature, or
  for imports). Queries should treat `turn_id IS NULL` as "not
  produced by an orchestrator turn" rather than as an error.
