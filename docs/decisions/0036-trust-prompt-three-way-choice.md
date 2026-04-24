# 0036 — Trust-prompt modal offers three outcomes, not two

**Date:** 2026-04-24
**Status:** accepted

## Context

`TextualPromptTrustGate` is the UI brick's real implementation
of `trust_policy="prompt"` for project convention files,
replacing `DenyingPromptTrustGate`. On first encounter with a
project, it pushes `TrustPromptModal` to ask the user whether
to load the discovered files.

The simplest shape is binary: allow or deny. A more nuanced
shape splits the allow case into "just this time" vs "trust
this project forever." Three options considered:

1. **Binary (allow / deny).** Simplest UX. "Allow" always
   persists to the allowlist; "Deny" never does. Problem: a
   user exploring an unfamiliar repo ("let me see what this
   `AGENTS.md` asks of the model") has no way to say "yes this
   time, decide later about forever." They're forced into a
   permanent decision on first encounter.
2. **Binary with separate allowlist-management command.**
   Prompt binary, but provide `cairn trust add/remove` for
   after-the-fact curation. Cleaner conceptually; worse UX —
   you have to *remember* to curate, and the allowlist grows
   by accretion until someone audits it.
3. **Three-way (trust-once / trust-project / deny).** The
   modal makes the distinction explicit at the moment the user
   has the context to reason about it. "Trust project"
   persists; "Trust once" is scoped to this process. "Deny"
   is also session-scoped — a future process will prompt
   again (the user didn't say "forever deny", just "not now").

## Decision

Three outcomes in `TrustPromptModal`:

- `o` / *Trust once* → `TrustPromptResult(decision=ALLOW, persist=False)`.
  Cached in the gate for this process; not written to the
  `AllowlistStore`.
- `p` / *Trust project* → `TrustPromptResult(decision=ALLOW, persist=True)`.
  Cached in the gate AND written to the allowlist via
  `AllowlistStore.add(project_root)`.
- `n` / *Deny* (or `Esc`) → `TrustPromptResult(decision=DENY, persist=False)`.
  Cached in the gate for this process; no persistence.

The gate's per-process decision cache (`dict[Path, TrustDecision]`)
means repeated `ConventionLoader.load()` calls in the same
session never re-prompt. The `AllowlistStore` check is the
first thing the gate does, so a project pre-approved in a
previous process bypasses the modal entirely.

A fourth outcome — "trust this filename everywhere forever" —
was considered and rejected for 0.10.0 (see ADR 0015 for the
analogous tool-approval conversation). It widens the policy
surface and crosses into trust-scope engine territory. Revisit
if users ask.

## Consequences

**Easier:**

- First-time encounters don't force a permanent decision. The
  user can explore a repo once without polluting the
  allowlist.
- The allowlist stays curated by intent — only entries the
  user explicitly chose to persist. No "accumulated from
  permissive defaults" drift.
- The modal's three outcomes map onto three keybindings that
  correspond to verbs the user can summarise without looking:
  *once*, *project*, *no*.

**Harder:**

- Three outcomes is more UI real estate than two. Mitigated by
  a compact button row and three one-letter keybindings.
- Users who want "always ask me" semantics have a distinction
  to reason about ("once" vs "project") they might not care
  about. Acceptable — the "once" option is the conservative
  default and `Trust project` is the explicit upgrade.

**Ongoing cost:**

- The gate's in-memory cache means process restart re-prompts
  unless the user chose "Trust project" last time. That's the
  design — a fresh process is a fresh trust boundary. No
  mitigation needed.
- If a fourth outcome ("trust filename pattern") lands later,
  it extends this modal without breaking the existing three
  — the `TrustPromptResult` shape accommodates new decision
  types by adding fields to the dataclass.
