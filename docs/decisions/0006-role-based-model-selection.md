# 0006 — Role-based model selection

**Date:** 2026-04-20
**Status:** accepted

## Context

A profile needs to name the models it uses: a primary model (the
companion's voice), a utility model (cheap and fast, for extraction
and compaction), and specialist models consulted via delegation
(reasoning, coding).

Naming options:

1. **Reference vendor IDs directly.** `primary_model = "claude-opus-4-7"`.
   Clearest, but every model upgrade is a config edit in every profile
   that uses the model.
2. **Reference roles and let cairn pick.** `primary_model = "role:primary"`.
   Adding a role is declarative; swapping which model is `primary` is a
   single edit.
3. **Both.** Accept either form.

A personal harness accumulates multiple profiles that share a handful
of models. Swapping a model across profiles should be one edit, not N.

At the same time, a profile that genuinely wants a specific vendor ID
(for an experiment, for a pinned model) should be able to say so.

## Decision

Accept both forms for model references:

- `"<vendor-id>"` — resolve to the `ModelConfig` with that `id`.
- `"role:<role>"` — resolve to the first `ModelConfig` with that role
  in its `roles` set.

The `ModelRole` enum enumerates roles: `primary`, `utility`,
`reasoning`, `coding`, `vision`, `fast`. A model declares which roles
it fulfils:

```toml
[[models]]
id = "claude-opus-4-7"
roles = ["primary", "reasoning"]
```

A `ModelRegistry` offers `by_id(…)`, `by_role(…)`, and
`resolve("role:primary"|"<id>")`.

## Consequences

**Easier:**

- Swapping the primary model across all profiles: change the `roles`
  metadata on one `[[models]]` entry.
- Profile definitions are readable: `role:primary` conveys intent;
  `claude-opus-4-7` conveys literal choice.
- Tests are explicit: a role is a named thing, with obvious expected
  behaviour.

**Harder:**

- Two models with the same role: `by_role` returns the first one the
  registry sees. Configuration-order-dependence is a smell; a future
  `priority` field may resolve it. For now, it's the user's
  responsibility to not duplicate a role.
- A typo in a role name (`"role:primry"`) is a configuration error
  caught only at resolution time. A stricter validator that checks
  every reference against known models is a future improvement.

**Ongoing cost:**

- The `ModelRegistry` is a dependency of anything that resolves a
  model — providers, profile loaders, the future orchestrator. It's
  a small dependency; threading it through is cheap.
