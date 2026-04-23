# 0031 — `trust_policy="prompt"` deferred to the UI brick

**Date:** 2026-04-23
**Status:** accepted

## Context

Arch doc §4.12 defines three trust policies for project
convention files:

- `prompt` (default) — on first encounter, show the user the
  file's path and first ~20 lines, require approval.
- `always` — load every project's files unconditionally.
- `project_allowlist` — load only from paths on a user-managed
  allowlist.

The convention-files brick ships before the UI brick. "Prompt on
first encounter" is fundamentally a UI interaction; there's no
headless way to ask the user mid-turn from the core library.

Three options for what happens when a user's profile still has
`trust_policy="prompt"` after this brick lands but before the UI
brick does:

1. **Silently fall back to `always`.** Convenient but misleading
   — users who chose `prompt` specifically wanted a safety gate,
   and silently removing it is the exact opposite of what they
   asked for.
2. **Silently fall back to `project_allowlist` with an empty
   allowlist.** Same effect as DENY, but provides no visible
   signal that anything is degraded.
3. **DENY with a visible WARNING (once per project per process).**
   Preserves the "this is gated" semantics the user chose, and
   makes the degradation visible so they can switch policy
   explicitly.

## Decision

`DenyingPromptTrustGate` — option 3. DENY every check, log
`WARNING: trust_policy='prompt' requires the UI brick (not yet
shipped). Switch to 'always' or 'project_allowlist' in your
profile config.` once per distinct project in a given process.

Rationale: the `prompt` default exists specifically so the user
gets a safety checkpoint. With no UI we can't ask, so we skip
rather than degrade the gate into something weaker that looks
the same.

## Consequences

- **Out-of-the-box experience**: until a user changes their
  `trust_policy`, no project convention files load. This matches
  the conservative default posture of security doc §3 ("treat
  untrusted input conservatively") but does mean "step one after
  install is often to switch trust policy."
- **Documented upgrade path**: `docs/conventions.md` tells users
  to set `trust_policy = "always"` for "I only ever work in my
  own repos" ergonomics, or `project_allowlist` + hand-edit
  `trusted_projects.toml` for safer fine-grained control.
- When the UI brick lands, a real `PromptingTrustGate` will
  replace `DenyingPromptTrustGate` in `trust_gate_for_policy()`.
  Profiles already configured with `trust_policy="prompt"` will
  get the real UX automatically — no migration, no config
  change required.
