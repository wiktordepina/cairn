# 0023 — Ship the JSONL observation log without the V1 rebuild path

**Date:** 2026-04-22
**Status:** accepted

## Context

Architecture doc §4.11 describes a per-day JSONL log as the
"source of truth" for observations, with SQLite as a derived index:
"the SQLite index can be rebuilt from it. Makes git-based
multi-machine sync trivial."

Implementing both the write path and the rebuild tool would stretch
the tier-1 brick's scope. The rebuild tool is only useful alongside
multi-machine sync, which isn't on the V1 roadmap.

## Decision

**V1 writes both the SQLite row and the JSONL line for every
observation, but ships no rebuild-from-JSONL tool.** The rebuild
path lands with the V3 multi-machine-sync brick.

The JSONL file shape matches `memory_entries` column-for-column
(via `Observation.model_dump_json()`) so the future rebuild tool is
a straight Pydantic parse + repo insert.

**No fsync per append.** Default
`MemoryConfig.observation_log_fsync = False`. SQLite is the real
durability store; losing the last few JSONL lines on process crash
is acceptable — those observations still exist in the database.

## Consequences

- **Positive.** V1 users who don't need multi-machine sync pay no
  observable cost: the log writes are cheap (no fsync), take no
  disk-space overhead worth mentioning, and are only read if/when
  the rebuild tool arrives.
- **Positive.** The V3 rebuild tool can assume the log format is
  stable — data written from 0.7.0 onward will be compatible with
  the eventual rebuild.
- **Negative.** The log is effectively dead weight until V3. If we
  cut V3, the log should be demoted to a debugging aid or removed.
- **Negative.** JSONL and SQLite can drift if a bug ships — writes
  happen under different paths. The extractor catches exceptions
  from either side and logs at ERROR, but doesn't attempt to roll
  back the other. V1 accepts the risk; tests cover the happy path
  and the store-failure path separately.

## Alternatives considered

**Skip the JSONL entirely.** Would break the arch-doc "source of
truth" invariant. If we cut V3, this becomes the right decision in
retrospect — but hedging is cheap.

**fsync per append for V1.** Slows extraction; provides durability
the primary SQLite store already delivers. Not worth it.

**Log to SQLite-backed append-only table instead of JSONL.** Would
duplicate storage without the git-friendly line-oriented property
that made JSONL attractive for eventual sync. Not worth the
migration.
