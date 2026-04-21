# 0013 — Tier taxonomy for tool risk (0-4 in V1)

**Date:** 2026-04-21
**Status:** accepted

## Context

Every tool in cairn carries a `risk_tier` integer. That number drives
the default approval policy, the middleware chain's decisions, and the
registry's scoping. The same shape appears in the security doc's
guidance on which calls should auto-approve, which need a visible log,
and which must always prompt.

Two design questions:

1. What does each tier mean?
2. How many tiers are useful?

Alternatives considered:

- **Single "is this dangerous" boolean.** Too coarse — "read a file"
  and "fire a missile" collapse to the same class.
- **A string enum (`safe | gated | dangerous`).** Loses the
  ordering that approvers rely on. Comparison becomes membership
  tests per approver.
- **An open integer scale (0..∞).** Flexible but loses the shared
  vocabulary between the spec, the ADRs, and the code.
- **Two axes (risk × side-effects).** Cleaner in principle but
  approvers would have to match against both. Side-effects is
  already a separate field for a different reason (informing
  transformers); using risk alone for the approval policy keeps the
  approver chain narrow.

## Decision

Five tiers in V1, numbered 0-4, with a fixed meaning per tier and a
fixed default approval policy per tier. Tier 5+ (arbitrary code
execution / shell) is deliberately out of V1 scope and rejected by
the `@tool` decorator; see [ADR
0016](0016-no-shell-in-v1.md).

| Tier | Shape | Default approval |
|---|---|---|
| 0 | Pure compute, no I/O | auto-approve |
| 1 | Scoped filesystem read | auto-approve |
| 2 | External read (network egress) | auto-approve with visible log |
| 3 | Mutating — scoped FS write, spending (delegation) | first-run prompt, then session allowlist |
| 4 | External side effects (email, posts, HTTP mutation) | always prompts |

The decorator enforces consistency between tier and side-effects at
registration time:

- Tiers 0-2 must be read-only (`side_effects ∈ {"none", "read"}`).
- Tier 3+ must not claim `side_effects="none"`.
- Tier 5+ is rejected outright.

The approvers in the default chain make decisions on the tier
directly: `AutoApproveReadOnly` approves tiers 0-2 that are also
read-only; `TierGate` escalates tier 4+ unconditionally. The
`SessionAllowlist` approver is tier-agnostic by design — it responds
to user behaviour ("approve this exact call again") rather than
policy.

## Consequences

**Easier:**

- Approvers are one-line predicates on `request.risk_tier` and
  `request.side_effects`. No string comparisons, no lookup tables.
- The tier number is portable documentation: users reading
  `docs/tools.md` see the same taxonomy that the code enforces.
- Adding a new tool is a question of "which row in the table do I
  fit?" — the answer is usually obvious, and the decorator's
  validation catches drift.

**Harder:**

- Cross-tier behaviour (a tier-2 tool that does a read AND triggers
  a side effect) has to pick the higher tier and accept the stricter
  policy. V1 has no such tool; if one arrives, we'd rather over-gate
  than under-gate.
- The tier-to-default-policy mapping is spread across
  `AutoApproveReadOnly` and `TierGate`. If we ever need a different
  mapping per deployment, the policy belongs in its own approver
  rather than a conditional inside an existing one.

**Ongoing cost:**

- The tier number surfaces in the UI and in approval prompts ("this
  is a Tier 3 call"). Users have to understand the taxonomy for the
  number to be meaningful. The docs carry their weight here.
