# 0016 — No shell / code-execution tool in V1

**Date:** 2026-04-21
**Status:** accepted

## Context

A `bash` (or `shell`, or `code_exec`) tool is the single most useful
thing you can add to a coding companion. It's also the single most
dangerous. Every major agent framework ships one, or is one.

The V1 question: do we ship it?

Arguments for:

- A companion helping with codebases genuinely needs to run tests,
  check types, lint, install packages, commit work. Without a shell,
  half of "coding companion" collapses into "code-review companion".
- A well-sandboxed shell tool can be reasonably safe. Linux namespaces,
  seccomp, and `bubblewrap`-style sandboxes are mature. Tools like
  `sandlock` wrap the lot with a policy file.

Arguments against:

- **Sandbox strength is a design decision with a long tail.** Which
  sandbox? Linux-only or cross-platform? What filesystem scope, what
  network policy, what resource caps, what escape audits? Every
  answer is a commitment with security consequences.
- **Default-approve is unsafe; default-reject is useless.** A Tier-5
  tool under the default approver flow prompts on every call. Users
  hit prompt fatigue and reflexively approve — same failure mode
  that makes browser-security UX hard. A shell tool without a good
  sandbox story ships with every user either rubber-stamping every
  prompt or disabling the tool.
- **The blast radius of a bad approval is catastrophic.** `rm -rf`,
  `curl | sh`, `git push --force`, `sudo`: every one of these is
  something the model might emit in a plausible-looking context.
  Even with spotlighting and approval, one fatigued click is
  unrecoverable.
- **Shipping a weak sandbox is worse than shipping no tool.** Users
  see "shell tool" and assume "safe to let the model run arbitrary
  shell" in a way they would never assume for raw `subprocess`.

The right move for V1 is to say "not yet" explicitly, with a
clear rationale and a statement of what we'd need to ship it
responsibly.

## Decision

V1 ships four built-in tools: `file_read`, `file_write`, `grep`,
`web_fetch`. None execute arbitrary shell commands or Python code.
The `@tool` decorator rejects `risk_tier=5` at registration time as
a belt-and-braces check.

The specific capabilities deliberately excluded:

- `bash` / `shell` — arbitrary shell command execution.
- `code_exec` — running Python snippets in-process or in a
  subprocess.
- `python_repl` — stateful Python evaluation.

Capabilities that almost look like shell execution but are actually
fine because they're scoped:

- `grep` — invokes `ripgrep` as a subprocess with argv-form only, no
  shell. The arguments are constructed by the tool itself from a
  typed Pydantic model; the model never passes through a shell.

If a future cairn version adds shell execution, it will land
alongside:

1. A chosen sandbox (likely Linux-first, `bubblewrap` or equivalent).
2. An ADR documenting the sandbox choice and its threat model.
3. A risk-tier assignment — most likely Tier 5, with no auto-approve
   path and a policy file governing what commands can even reach
   the approval prompt.
4. An honest statement in the docs about residual risk.

Until those pieces are in place, the answer is no.

## Consequences

**Easier:**

- The V1 attack surface is bounded: filesystem (sandboxed),
  network (SSRF-defended). No arbitrary command execution means
  no arbitrary command-execution vulnerabilities.
- The risk-tier taxonomy caps cleanly at 4, matching the approver
  chain's discrimination.
- Users can read the tool catalogue and know exactly what the
  companion can and cannot do.

**Harder:**

- Genuine coding work (running tests, lint, installs, git) needs the
  user to alt-tab out of the companion and do it themselves. Cairn
  is less useful for iterative development than frameworks that
  ship `bash`.
- "Why can't it just run `pytest` for me?" will be a common
  question. The answer is a section of `docs/tools.md` plus this
  ADR.

**Ongoing cost:**

- Every release cycle revisits this decision: "is the sandbox story
  mature enough yet?" The bar stays high. Shipping unsandboxed
  shell because it's convenient is not on the table.
- The design doc explicitly calls out `bash` as "V2 at earliest,
  probably never in its current shape". If we do ship it, it'll be
  after a deliberate design round, not a side door.
