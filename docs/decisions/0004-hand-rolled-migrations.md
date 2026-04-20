# 0004 — Hand-rolled SQL migrations over an ORM / framework

**Date:** 2026-04-20
**Status:** accepted

## Context

Schema migration tools for Python land on a spectrum from "ORM
migration generator" (Alembic, Django migrations) to "just run the SQL
files in order" (the approach most hand-rolled scripts take). The first
offers auto-generation from model diffs, rollback helpers, and a rich
DSL; the second offers absolute clarity about what's going to happen
to the database.

Relevant constraints for cairn:

- **No ORM.** Cairn uses `aiosqlite` directly, with handwritten SQL per
  repository. There are no SQLAlchemy models to diff.
- **SQLite-only.** No need for database-agnostic DSL.
- **Single-user.** Migrations run at process startup; there's no
  multi-instance coordination problem.
- **Small team (one person).** A tool's learning curve is paid by
  everyone.
- **Forward-only is acceptable.** Rollback isn't a V1 requirement.

An ORM migration tool for a project without an ORM is mostly overhead:
a DSL to learn, a separate `env.py`, an abstraction that hides what the
actual SQL is going to do.

## Decision

Migrations are hand-rolled numbered SQL files in
`src/cairn/persistence/_sql/`:

```
_sql/
  0001_initial_schema.sql
  0002_orchestrator_tables.sql    (planned)
  …
```

A `schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)`
tracking table records what's been applied. On startup:

1. The migration runner compares the sequence of files on disk with
   what's in `schema_migrations`.
2. Gaps (e.g. missing `0003` with `0004` applied) are an error.
3. A DB version ahead of the code is an error — cairn refuses to
   downgrade a database it doesn't know how to read.
4. Missing migrations are applied in sequence, each in a transaction.

Migrations are loaded via `importlib.resources` so they survive
packaging.

## Consequences

**Easier:**

- Every schema change is plain SQL in a plain file. Diff-able,
  reviewable, executable by hand for debugging.
- No DSL, no framework to learn, no auto-generation surprises.
- Tests exercise the real migration path — there's no "models" code to
  keep in sync with the schema.

**Harder:**

- No rollback helper. When a migration is bad, fix forward with a new
  migration. Forward-only is a policy, not a limitation of the tooling.
- No data-transform helpers. A Python migration option may be added
  when a migration genuinely needs Python logic (e.g. a JSON column
  rewrite); for now, pure SQL suffices.

**Ongoing cost:**

- Naming discipline: every new migration gets the next sequential
  number. Two people writing migrations on parallel branches must
  coordinate the numbering (irrelevant at team size 1; worth
  remembering if that changes).
