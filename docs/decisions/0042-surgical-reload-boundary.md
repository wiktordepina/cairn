# 0042 — Surgical reload boundary for `/reload`

**Date:** 2026-04-25
**Status:** accepted

## Context

The 0.15.0 file-watcher brick adds drift detection for config layers,
convention files, and profile docs (soul / user-context /
MEMORY.md). When the user runs `/reload`, we need to apply the
on-disk changes to the running process without losing the active
session, the in-flight extraction queue, or the open Textual UI.

Two extreme designs were on the table:

- **Full re-bootstrap.** Tear down the orchestrator + persistence
  layer + extraction worker, then re-run `_run(config)` from the top.
  Conceptually clean, single code path. Costs: drops queued
  extractions, kicks the DB connection, cancels any active turn,
  resets the clock state, and forces the Textual screen stack to
  be rebuilt from scratch.
- **Surgical swap.** Re-load `CairnConfig` from disk, build new
  instances of the collaborators that read the config (provider
  registry, model registry, secret resolver), reuse the
  collaborators that hold runtime state, drop the loader caches,
  and re-snapshot the watcher. The orchestrator instance, active
  session, and persistence remain bound to live state.

A full re-bootstrap is the simpler implementation but the worse
user experience: every `/reload` would feel like a restart. The
surgical swap preserves "I'm still in the same conversation"
semantics, which is the whole point of hot reload.

## Decision

`/reload` performs a **surgical swap**, not a re-bootstrap.

What the `Reloader` rebuilds and swaps onto the running orchestrator:

- `SecretResolver` — fresh instance, prompt cache cleared, new
  resolution against keyring / env / prompt schemes.
- `ModelRegistry` — rebuilt from `new_config.models`.
- `ProviderRegistry` — rebuilt with the new `SecretResolver`. Old
  provider clients drain naturally on next iteration; we hold no
  abortable handles.

What the `Reloader` invalidates (caches only — instances preserved):

- `ConventionLoader.invalidate()` — next turn re-reads convention
  files from disk.
- `ProfileDocLoader.invalidate()` — next turn re-reads soul /
  user-context / MEMORY.md.
- `FileWatcher.resnapshot(...)` — drops the dirty set and replaces
  the baseline with the current on-disk state.

What the `Reloader` does **not** touch (instances + state preserved):

- The `Orchestrator` itself — same object before and after. The
  per-session cancel flags and currently-running turn are
  preserved.
- Active session, persisted messages, in-flight provider stream
  if a turn is mid-flight.
- `Database` / aiosqlite connection.
- `MemoryService` and `ObservationExtractionQueue` — these hold
  their own `MemoryConfig` reference. V1 trades stale memory
  config (rare to change at runtime) for simpler reload.
- The Textual app, its screen stack, and the widget tree.

## Consequences

**Pros:**

- A `/reload` feels surgical: the active conversation continues,
  costs accumulate on the same `usage_repo` rows, the extraction
  worker keeps draining its queue.
- Mid-turn `/reload` queues until the running turn completes —
  the in-flight provider stream finishes against the bound
  registries, and the swap takes effect on the next turn. No
  partial-state weirdness.
- Validation failures (`load_config` raising `ConfigError`) are
  recoverable: `Reloader` returns `ReloadResult(ok=False)` without
  touching any collaborator. The user keeps the previous-good
  config and the watcher's drift banner stays up until they fix
  the file and reload again.

**Cons / accepted limitations:**

- Memory and compaction config changes don't take effect at
  `/reload` time — they're snapshotted onto `MemoryService` and
  `Compactor` at boot and stay frozen until restart.
- A new `Database` path in config also doesn't apply at reload —
  the live aiosqlite connection points at the boot-time path.
  Documented under "Hot reload" in `docs/configuration.md`. If
  someone needs to switch profiles or DB paths, restart.
- Provider-registry replacement is full-instance-swap, so any
  custom provider adapters registered via
  `provider_registry.register_adapter(...)` after boot are lost.
  The bootstrap re-registers all built-in adapters; user-registered
  adapters need to re-run their registration. This is theoretical
  in V1 — no code path registers adapters outside the bootstrap.

**Why not a smaller swap?** Previously we considered swapping only
the `SecretResolver` (cache invalidation) and leaving the
registries alone. That misses the case where a `models` block was
edited or a new provider was added — the user would `/reload` and
nothing would visibly change until restart. Full registry swap is
the smallest change that actually applies common config edits.

**Why not also swap `MemoryService` / extraction queue?** They
hold a live SQLite connection and an active worker task. Swapping
either mid-process means draining and re-spawning the worker,
which is a meaningful UX hiccup. The config fields they read
(`MemoryConfig` knobs) are very rarely changed at runtime. If a
user does want a memory-config change to take effect, restart.

**Why not rebind the active session's `model` when a role pin
moves?** A session's `Session.model` is a concrete id (e.g.
`claude-opus-4-7`), resolved from `role:primary` at session
creation. If the user edits `roles` to move `primary` to a
different model, the orchestrator's per-turn
`model_registry.resolve(session.model)` still resolves the *old*
id (which still exists; it just no longer carries the primary
role) — so the active session continues with the old model.

We deliberately do **not** auto-rebind. Mid-session model swaps
have provider-specific failure modes that are subtle, hard to
detect from history, and easy to surprise the user with:

- **Tool-call id schemes.** Anthropic mints `toolu_*` ids,
  OpenAI mints opaque strings. Switching providers mid-session
  means a previously-issued id appears in history under the new
  provider's `tool_call_id` field — strict providers reject this
  with a 400.
- **Thinking blocks.** Anthropic round-trips `ThinkingBlock`s
  explicitly. DeepSeek requires `reasoning_content` to be echoed
  back per its own contract. OpenAI strips thinking entirely.
  Provider switches drop or reject these inconsistently.
- **Tool support changes.** History contains tool calls; new
  model has `supports_tools=False`. Some providers ignore the
  blocks; some 400.
- **Prompt-cache namespaces.** Each provider has its own. Mid-
  session swap throws away the cache; user pays the rebuild
  silently.

The Reloader detects role-pin drift (active session's model id ≠
the new resolution of `active.primary_model`) and surfaces a hint
banner: *"primary role now resolves to X; the active session
stays on Y. Restart cairn to switch."* The active session stays
pinned. Future sessions pick up the new role binding.

In-session model switching is a separate feature, gated on a
future explicit `/model <id>` command with provider-compatibility
checks and opt-in confirmation.

## Status

Accepted. Captured in `.plan/file-watcher-brick-design.md` §6 + §7;
implemented across commits 5–7 of the file-watcher brick.
