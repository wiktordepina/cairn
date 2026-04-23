# 0030 — Convention-file ordering: user-level before project, broad-before-specific

**Date:** 2026-04-23
**Status:** accepted

## Context

Cairn reads multiple convention files per session:

- zero or more user-level fallback files
  (e.g. `$XDG_CONFIG_HOME/cairn/CAIRN.md`),
- the nearest project file per configured filename (`CAIRN.md`,
  `AGENTS.md`, `CLAUDE.md` by default), walked up to the git root,
- any additional matches in nested directories under cwd when
  `search_subdirs` is true.

All of them ride in the `<project_conventions>` segment of the
system prompt. The question is the order in which we stack them.

## Decision

**Broad → specific:**

1. User-level files, in declared `user_level_paths` order.
2. Project files matched by ancestor walk, nearest-wins, in the
   `filenames` config order.
3. Project files matched by nested descent, shallower first.

The model reads all of them — a later file does not mechanically
override an earlier one. But when two files contradict, the
convention we want is that **more specific context wins**: a
per-package `AGENTS.md` should override a repo-root one; a
repo-root one should override the user's global default.

Placing broader files earlier and more specific files later lines
up with standard prompt-ordering intuition ("recency wins"):
re-asserting a fact later in the prompt is the cleanest way to
signal precedence without the model having to reconcile explicit
priority metadata.

## Consequences

- Users get predictable ordering without having to configure it.
- A user can layer "my defaults → this project → this package"
  without editing the project's convention files.
- The trust gate only applies to project files — user-level
  files are under the user's own config dir, same trust boundary
  as soul document / user-context / MEMORY.md. Inconsistent
  treatment of home-dir content would be a worse precedent to
  set than the minor asymmetry this introduces.

## Alternatives considered

- **Explicit priority on each config entry.** Rejected as
  unnecessary ceremony — the natural layering covers the common
  case, and users who truly want a different precedence can
  achieve it by re-ordering `user_level_paths` or renaming files.
- **Load all files in `filenames` config order with no
  broad-before-specific rule.** Rejected because a user-level
  fallback file with the same basename as a project file would
  then appear after the project file on some sessions and before
  it on others depending on declaration quirks.
