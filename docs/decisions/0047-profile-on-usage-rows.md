# 0047 — `profile` column on `model_usage`

**Date:** 2026-04-26
**Status:** accepted

## Context

V0.18.0's `/cost` extension renders a multi-window report split
into "current profile" and "all profiles" columns. Today, the
`model_usage` table has no profile attribution: rows know which
session they belong to, but neither `model_usage` nor `sessions`
carries a profile name.

Three options for tagging rows with a profile:

1. **Denormalise** — add a `profile` column to `model_usage`,
   stamped at record time.
2. **Join through `sessions`** — store profile on the session
   row and join when querying.
3. **Stay derivable** — keep no column; reconstruct profile
   attribution from `[providers.*]` / `[models.*]` ↔ row.model.

(3) is brittle and historically wrong (model assignments change
over time). (2) is normalised but `sessions` itself doesn't
carry a profile column today either, so the join needs a new
column somewhere — and `model_usage` is the hot table for the
`/cost` query.

## Decision

**Add a nullable `profile TEXT` column to `model_usage`** plus a
`(profile, timestamp)` index. The orchestrator's
`BasicCostTracker` stamps the active profile on every
`record()` call.

Migration `0004_usage_profile.sql` is a single ALTER + CREATE
INDEX. Existing rows stay NULL — interpreted as "before profile
tracking", honestly reported in the all-profiles total but
excluded from current-profile filters.

`UsageRepo.cost_in_window_by_profile` filters on the column;
`UsageRepo.cost_summary` fans out the seven SUMs the `/cost`
report needs in parallel via `asyncio.gather`.

## Consequences

- Adding a column is a two-line migration.
- Per-profile aggregations stay cheap as the table grows — the
  new index covers the hot path.
- The `model_usage` write path grows one new keyword
  argument; everything else is mechanical.
- Future `cairn cost` CLI subcommand can hit the same
  `cost_summary` API.
- A user editing `[profiles.*].name` mid-history doesn't
  retroactively rewrite old rows. That's correct: rows reflect
  what was active when they were recorded.

## Alternatives considered

- **Profile on `sessions` + JOIN**: works, but requires a
  schema change on `sessions` *and* every aggregation grows a
  join. More code per query for a column that is simpler to
  denormalise.
- **No backfill of historical rows**: chosen. Backfilling NULL
  rows would require guessing — and the data we'd guess from
  (current `[providers.*]` mappings) is itself mutable. NULL
  is the honest representation of "unknown".
- **Drop NULL rows from all aggregations**: rejected. They
  represent real spend; hiding them from the all-profiles
  total would understate the user's overall position.
