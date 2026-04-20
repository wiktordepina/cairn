# Persistence

Cairn stores all conversation, tool-call, and usage data in SQLite, with
one database per profile.

## On-disk layout

```
$XDG_DATA_HOME/cairn/              (0o700)
├── <profile-1>/                   (0o700)
│   ├── cairn.db                   main database
│   ├── cairn.db-wal               WAL file (SQLite-managed)
│   ├── cairn.db-shm               shared-memory index (SQLite-managed)
│   └── memory/                    reserved for observation JSONL (planned)
└── <profile-2>/
    └── ...
```

- `$XDG_DATA_HOME` defaults to `~/.local/share` on Linux.
- Directory permissions are `0o700` — owner read/write/execute only.
  Applied to both the `cairn/` root and each `<profile>/` subdirectory.
- Profile names are sanitised at write time: only alphanumerics, `-`,
  and `_` are allowed. Path-traversal attempts are rejected.

## Why per-profile

Each profile gets its own database for three reasons:

1. **Total isolation.** A `work` profile's sessions and memories cannot
   accidentally surface in a `personal` profile.
2. **Backup and sync are simple.** Copy the profile directory. Done.
3. **Schema evolution is per-profile.** A future breaking migration
   only needs to run once, on the profiles you actually use.

See [ADR 0001 — Per-profile SQLite](decisions/0001-per-profile-sqlite.md)
for the full reasoning.

## Tables

The V1 persistence brick ships four tables. A fifth, `memory_entries`
(plus FTS5 shadow), lands with the memory brick.

### `sessions`

One row per session. Companion, persona, and ephemeral sessions all
live here, distinguished by the `type` column.

Key columns: `id`, `type`, `persona`, `model`, `memory_space`, `title`,
`created_at`, `updated_at`, `archived`, `parent_session_id`.

- **`memory_space`** enforces tenant isolation at query time. Repository
  methods that filter sessions require an explicit space; cross-space
  reads (`list_recent`) are deliberately named so they can't be called
  by accident.
- **`parent_session_id`** threads delegation sub-sessions back to their
  parent. Used for cost aggregation across the session tree.
- **`archived`** is a soft-delete flag. V1 has no hard delete for
  sessions — you archive and the UI hides them. (Hard delete may land
  later; it's not a V1 requirement.)

### `messages`

One row per message. The `content` is stored as JSON in a `content_json`
column, holding a list of `ContentBlock` discriminated-union objects
(`TextBlock`, `ToolUseBlock`, `ToolResultBlock`, `ImageBlock`,
`ThinkingBlock`).

- **`UNIQUE(session_id, idx)`** — every message has a monotonic index
  within its session. `idx` is assigned server-side under a
  `BEGIN IMMEDIATE` transaction so parallel appends to the same session
  don't race.
- **FK CASCADE** — archiving (not deleting) keeps messages; deleting a
  session row cascades to its messages.

### `tool_calls`

One row per tool invocation. The lifecycle (`pending → approved →
executing → completed | failed | timed_out | cancelled`) is tracked by
the `status` column. Transitions are conditional UPDATEs — attempting
an illegal transition raises `InvalidToolCallTransition` rather than
silently corrupting state.

Columns include `tool_name`, `arguments_json`, `output_json`,
`output_bytes` (original size before truncation), `error_class`,
`error_message`, `duration_ms`, and `delegation_sub_session_id` (FK to
`sessions` for delegation tools).

See [ADR 0008 — Tool-call state as a column](decisions/0008-tool-call-state-column.md).

### `model_usage`

One row per provider call. This is the single source of truth for cost
reporting.

Notable: **no FK CASCADE from sessions.** Deleting a session does *not*
delete its usage rows. Cost accounting survives session deletion so you
can still answer "how much did I spend last month?" after archiving.

Aggregations exposed by `UsageRepo`:

- `total_cost_for_session(session_id)` — direct costs.
- `total_cost_for_session_tree(session_id)` — includes delegation
  sub-sessions.
- `cost_in_window(start, end)` — arbitrary time window.
- `cost_today_utc()` — day's total.
- `cost_per_turn(session_id)` — breakdown by turn.
- `by_operation_in_window(start, end)` — primary turn vs. delegation vs.
  extraction, etc.
- `by_model_in_window(start, end)` — cost per model.

## WAL mode

Connections run with:

- `journal_mode = WAL` — concurrent readers don't block writers.
- `synchronous = NORMAL` — balance of durability and speed.
- `foreign_keys = ON` — not the default in SQLite.
- `busy_timeout = 5000` (5s) — retry under contention.
- `temp_store = MEMORY` — temp tables in RAM.

The `cairn.db-wal` and `cairn.db-shm` files are SQLite-managed. They
are part of the database — don't copy just `cairn.db` for a backup.

## Backup

Three options, easiest first:

1. **Stop cairn, copy the profile directory.** Simple; guaranteed
   consistent; no tooling.
2. **SQLite online backup.** Cairn exposes `.backup` through the
   repository layer (planned). Safe while cairn is running.
3. **File-system snapshot** (ZFS, btrfs, LVM). Consistent per snapshot;
   the WAL replays on next open.

Do not copy `cairn.db` alone while cairn is running. The WAL may contain
writes not yet checkpointed into the main file; copying just the main
file loses them.

## Migrations

Schema changes are hand-rolled numbered SQL files in
`src/cairn/persistence/_sql/`. The `schema_migrations` table records
what's been applied. On startup:

- Applied-but-missing migrations (gaps in the numeric sequence) are an
  error.
- A DB schema version ahead of the code is an error — cairn refuses to
  downgrade your data.
- Missing migrations are applied in a single transaction.

See [ADR 0004 — Hand-rolled SQL migrations](decisions/0004-hand-rolled-migrations.md).

## Memory-space isolation

Repository methods that read session-scoped data require the caller to
pass the `memory_space`. Cross-space methods exist (for features like
"list recent sessions") but are explicitly named:

- `list_for_space(space=<str>)` — scoped read. Safe by default.
- `list_recent(limit=N)` — cross-space. Called by name.

The goal is that *accidental* cross-space leakage requires calling a
deliberately-named method. It's enforcement-by-API, not enforcement-by-
convention.

See [ADR 0007 — Memory-space scoping at the repository layer](decisions/0007-memory-space-at-repo-layer.md).

## Related ADRs

- [0001 — Per-profile SQLite](decisions/0001-per-profile-sqlite.md)
- [0004 — Hand-rolled SQL migrations](decisions/0004-hand-rolled-migrations.md)
- [0007 — Memory-space scoping at the repository layer](decisions/0007-memory-space-at-repo-layer.md)
- [0008 — Tool-call state as a column](decisions/0008-tool-call-state-column.md)
