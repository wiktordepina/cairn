# Project conventions

Cairn reads Markdown convention files from the project you open it
in — build commands, coding conventions, domain vocabulary, things
to avoid. They're orthogonal to cairn's identity: they tell the
model how *this codebase* works, not who cairn is or who the user
is.

This page covers how discovery works, how the trust gate protects
you from instructions smuggled in via a freshly cloned repo, and
how to configure both per profile.

!!! note "V1 scope — discovery + loading"
    V1 ships: discovery, the trust gate with `always` /
    `project_allowlist`, loading, size-capped paragraph
    truncation, and injection into the system prompt. The
    `/conventions` slash command and the interactive
    first-encounter prompt (`trust_policy="prompt"`) land with
    the UI brick.

## What files get loaded

The default `filenames` list is `["CAIRN.md", "AGENTS.md",
"CLAUDE.md"]`. `AGENTS.md` is the de facto cross-tool standard;
`CLAUDE.md` is Claude Code's native filename; `CAIRN.md` is
cairn's own. See
[ADR 0032](decisions/0032-convention-files-no-cursor-cline.md) for
why Cursor's `.mdc` and Cline's `.clinerules` are not in the
default list.

You can override the list per profile:

```toml
[profiles.work.convention_files]
filenames = ["CAIRN.md", "AGENTS.md", "CLAUDE.md", "GEMINI.md"]
```

## Discovery

Starting from the current working directory, cairn walks up the
directory tree to a boundary (`walk_up_to` — default `git_root`)
and takes the **nearest** match per filename. If `search_subdirs`
is true (default), additional matches in nested directories under
cwd are also emitted, shallower first.

Discovery skips:

- common build / dependency dirs (`.git`, `node_modules`,
  `.venv`, `venv`, `__pycache__`, `.mypy_cache`, `.pytest_cache`,
  `.ruff_cache`, `.tox`, `dist`, `build`, `target`);
- hidden directories below cwd (`.github`, `.cache`, …);
- symlinks (we don't follow them during discovery — avoids
  surprises from `link → /etc`).

The nested descent is capped by `max_nested_depth` (default 3) so
a huge monorepo doesn't turn discovery into a full tree walk.

## Assembly order

Inside the `<project_conventions>` segment of the system prompt,
files are stacked broad-to-specific per
[ADR 0030](decisions/0030-convention-file-ordering.md):

1. User-level fallback files (in declared `user_level_paths`
   order).
2. Project files matched by ancestor walk (nearest-wins, in
   `filenames` order).
3. Project files matched by nested descent (shallower first).

## User-level fallback files

You can declare convention files outside the project so
cross-project preferences don't need duplication:

```toml
[profiles.work.convention_files]
user_level_paths = [
    "$XDG_CONFIG_HOME/cairn/CAIRN.md",
    "~/.claude/CLAUDE.md",
]
```

`~` and `$VAR` are expanded at load time. Missing files are
skipped silently — they're optional fallbacks.

User-level files bypass the trust gate. They live in your own
config directory, same trust boundary as your soul document and
MEMORY.md.

## The trust gate

Project convention files come from the repo, which may be code
you cloned five minutes ago. The model reads these as
instructions, so we run project files through a trust gate
before loading. User-level files are exempt (see above).

Configure via `trust_policy`:

```toml
[profiles.work.convention_files]
trust_policy = "project_allowlist"
```

### `trust_policy = "prompt"` (the default)

On V1, this policy currently **denies** all project files with a
visible WARNING, because interactive per-project prompting
requires the UI brick. When the UI lands, the same setting will
switch to "on first encounter, show you the file and ask" — no
config change required. See
[ADR 0031](decisions/0031-trust-prompt-deferred.md).

Until then, choose one of the explicit policies below.

### `trust_policy = "always"`

Load every project's convention files unconditionally. Convenient
if you only ever work in your own repos; risky if you clone and
run cairn in unfamiliar code. Every project's `AGENTS.md` goes
straight into the model's system prompt.

### `trust_policy = "project_allowlist"`

Only load convention files from projects explicitly trusted in
`$XDG_CONFIG_HOME/cairn/trusted_projects.toml`. Schema:

```toml
[[projects]]
path = "/home/user/projects/cairn"
added_at = "2026-04-23T10:30:00Z"
```

Paths are resolved. Descendants of a trusted path are trusted
automatically, so adding a repo root trusts any working directory
inside it.

In V1 you hand-edit this file. A `cairn trust add` / `trust list`
/ `trust remove` CLI ships with the CLI brick — the
`AllowlistStore` API it calls is already in place.

If the file is missing or malformed, the allowlist is empty; a
malformed file is backed up to `trusted_projects.toml.broken` on
first read so you can recover it manually.

## Size caps and truncation

Each file is capped at `max_bytes_per_file` (default 64 KiB) —
well above the size of any reasonable convention file. Files
over the cap are truncated at a paragraph boundary and the
content ends with an HTML comment indicating the original size:

```markdown
…last paragraph that fit…

<!-- truncated: original was 102400 bytes -->
```

The comment is visible to the model but renders as invisible
whitespace in Markdown viewers.

## Binary / unreadable files

A convention filename with binary content (null bytes in the
first 1 KiB) is skipped with a WARNING. Unreadable files log a
WARNING and skip too. Neither case breaks the load — you'll
still get every file that IS readable and text.

## What the model sees

Each loaded file becomes one `<project_conventions>` envelope in
the system prompt:

```
<project_conventions source="AGENTS.md" path="/home/user/projects/cairn/AGENTS.md">
# Project conventions for this repository
# (content here)
</project_conventions>
```

The `source` attribute carries the basename; `path` carries the
absolute path. Multiple envelopes stack with a blank line between
them.

## Reloading

Convention files are stable per-session — they're cached on first
read and re-used for every turn until `invalidate()` is called.
The `/reload` slash command (UI brick) will drop the cache on
demand. Until then, restarting cairn picks up edits.

## API reference

See [`cairn.conventions`](reference/conventions.md) for the full
module API, including `ConventionLoader`, `TrustGate` and the
three concrete gates, `AllowlistStore`, and the envelope
rendering helpers.
