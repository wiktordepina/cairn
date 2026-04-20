# 0002 — Three-tier config merge (user / project / local)

**Date:** 2026-04-20
**Status:** accepted

## Context

Cairn runs in two overlapping contexts: a personal, long-lived
relationship with a companion (which has the user's global preferences)
and a per-project working surface (which may have project-specific
overrides — different convention-file rules, different model choices,
different budgets). Two flat config files don't cover this; one flat
file forces the user to repeat boilerplate across projects.

Patterns considered:

1. **Single file.** Simple, but no project-specific overrides.
2. **User file + project file with project-wins precedence.** Covers
   most of the need but forces personal tweaks to land in a checked-in
   project file.
3. **User + project + local** (three layers): personal defaults,
   project overrides (checked in, shared with the team), and local
   overrides (gitignored, your personal per-project tweaks).

We also considered CLI flags as a fourth effective layer (they are —
see below).

## Decision

Config is merged in this order, with later tiers winning:

```
user  <  project  <  local  <  CLI flags
```

- **User** — `$XDG_CONFIG_HOME/cairn/config.toml`. Personal global
  defaults.
- **Project** — `<repo>/.cairn/config.toml`. Team-level overrides.
  Checked into the repo.
- **Local** — `<repo>/.cairn/config.local.toml`. Personal per-project
  tweaks. Gitignored.
- **CLI flags** — highest priority, per-invocation.

Project discovery walks upward from the current directory looking for a
`.cairn/config.toml`, stopping at the git root by default.

Active-profile resolution follows the same precedence: `--profile` flag
> local > project > user.

## Consequences

**Easier:**

- Teams can commit `.cairn/config.toml` as a shared baseline without
  forcing every developer to share the same local preferences.
- Individual developers keep their API keys and per-machine paths in
  `config.local.toml` without risk of checking them in.
- Adding ad-hoc overrides for experimentation is a CLI flag.

**Harder:**

- Debugging "why is this value what it is?" requires understanding the
  merge. A `cairn config show` command (planned) and a future
  `config diff` view mitigate this.
- Three-way merges for `delegation_tools` arrays and similar list-valued
  fields require a merge policy. The current policy is "replace, don't
  concatenate" — simpler and less surprising than concatenation.

**Ongoing cost:**

- The loader carries explicit tier metadata alongside values so
  eventual tooling can answer "which file provided this value?".
