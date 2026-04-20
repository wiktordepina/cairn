# 0001 — Per-profile SQLite database

**Date:** 2026-04-20
**Status:** accepted

## Context

Cairn is a single-user tool, but a single user juggles multiple contexts:
a `companion` profile for personal use, a `work` profile that answers to
a different identity and budget, perhaps an `experimental` profile for
trying new models. Each profile has its own soul document, memory
contents, budget caps, and session history.

The storage options we considered:

1. **One shared database, `profile` column on every row.** Ergonomic for
   cross-profile queries ("which profile did I spend the most on this
   month?"). A single bug in a `WHERE profile = …` clause leaks data
   across profiles.
2. **One database per profile.** Isolation is structural. Backup and
   migration operate per-profile. Cross-profile queries become
   application-level joins, which is fine — they're rare.
3. **One file per concern per profile** (sessions.db, memory.db, …).
   Complexity with little payoff; SQLite handles multi-table access
   just fine in one file.

The user-facing consequence we care about most: if something goes
catastrophically wrong with a profile's data, it must not touch the
others.

## Decision

Each profile gets its own SQLite file at
`$XDG_DATA_HOME/cairn/<profile>/cairn.db`. The enclosing directory is
created with `0o700` permissions. Profile names are sanitised at write
time (alphanumerics, dash, underscore only) so the path cannot be used
to escape the cairn data root.

Cross-profile operations are explicit at the application layer —
there is no cross-profile SQL.

## Consequences

**Easier:**

- Backup: `cp -a $XDG_DATA_HOME/cairn/<profile> ./backup/` is a complete
  snapshot.
- Sync: put the profile directory under version control (git,
  Syncthing) per machine.
- Migration: a forward-only schema change runs once per profile the user
  actually opens, not once against a giant shared DB.
- Isolation in depth: the memory-space invariants enforced at the repo
  layer are a second line of defence, not the only one.

**Harder:**

- Cross-profile cost reporting requires querying multiple databases.
  Deferred until there's a real user story for it.
- Testing cross-profile behaviour requires more setup than a single DB.
  Acceptable — the cross-profile surface is intentionally thin.

**Ongoing cost:**

- Slightly more I/O setup per process: one `Database` instance per
  profile cairn holds open. In practice, cairn only opens the active
  profile plus, transiently, others for admin commands.
