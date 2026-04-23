# 0032 — No support for Cursor `.mdc` / Cline `.clinerules` in V1

**Date:** 2026-04-23
**Status:** accepted

## Context

Arch doc §4.12 enumerates the convention-file landscape as of
April 2026:

| File | Origin |
|---|---|
| `AGENTS.md` | Open spec, Linux-Foundation-stewarded |
| `CLAUDE.md` | Claude Code |
| `GEMINI.md` | Gemini CLI |
| `.cursor/rules/*.mdc` | Cursor |
| `.clinerules` | Cline |
| `CAIRN.md` | Cairn |

The table lists all of them for landscape completeness, not as a
ship list. The V1 brick ships the default `filenames` list as
`["CAIRN.md", "AGENTS.md", "CLAUDE.md"]`.

## Decision

V1 ships three filenames in the default list. Cursor's
`.cursor/rules/*.mdc` and Cline's `.clinerules` are **not** in
defaults and **not** supported by the discovery logic.

Reasons:

- `.mdc` is a directory glob (multiple files per rule-set), not a
  single file — supporting it requires a different shape of
  discovery than the walk-up-and-take-one pattern we have. It
  would either force restructuring the discovery logic or
  becoming a second discovery path; neither is worth the
  complexity without a user asking for it.
- `.clinerules` is supported only by Cline and has no
  corresponding open spec. Its format is not plain Markdown
  (it's an inline-comment-heavy plaintext format specific to
  Cline's parser). Including it by default leaks Cline-isms into
  every cairn session.
- `GEMINI.md` is plain Markdown and would be trivially
  supportable by adding it to defaults, but we chose not to —
  every filename in the default list increases the surface area
  that a cloned repo can use to smuggle instructions into the
  model's system prompt.

## Consequences

- Users who want Cursor / Cline / Gemini compatibility can add
  the relevant filenames to `convention_files.filenames` in
  their profile. For `.mdc` they're out of luck until proper
  glob support lands.
- We reserve the right to move these into defaults later once a
  user with a concrete need asks. The bar is "real user, real
  workflow," not "would be nice to have."
- This ADR intentionally keeps defaults conservative — the
  security doc's threat model (§3) treats every convention file
  as untrusted prompt input, and the default list should be the
  minimum users likely already want, not the maximum we could
  support.
