# 0035 — "Remember for this session" checkbox gated to tier ≤ 3

**Date:** 2026-04-24
**Status:** accepted

## Context

The UI brick's `ApprovalModal` lets the user approve or reject a
pending tool call. For tier-3 calls (mutating filesystem ops,
delegation), re-approving the same `(tool_name, args)` tuple on
every iteration of an edit session is tedious and trains the user
to approve reflexively. [ADR 0015](0015-session-allowlist-exact-match.md)
established the runtime contract:
`SessionAllowlist` caches `(tool_name, sha256(sorted_json(args)))`
tuples, and a subsequent identical call skips the modal.

The modal needs a visible affordance to populate that allowlist.
Two axes to decide:

1. **Who is the affordance for?** Every tool with an approval
   gate, or only the ones the user is likely to invoke
   repeatedly?
2. **What's the default state?** On (opt-out) or off (opt-in)?

Tier-4+ tools in V1's taxonomy are the "network side-effects,
external mutations, credential-touching" class. The whole point
of tier-4 is that each invocation deserves conscious approval —
the user looks at *this specific call's* args, not at a class of
behaviour. Letting the modal offer a "remember this" affordance
for tier-4 would normalise a pattern that shouldn't exist.

Tier-3 is different. File writes under a sandboxed workspace with
explicit paths are genuinely repeatable: an iterative
"read → edit → save" loop fires the same `(file_write, path=X)`
tuple multiple times in the same session, and the user only cares
about approving *X* once.

## Decision

The "Remember this exact call for the session" checkbox in
`ApprovalModal` is:

- **Rendered for every call** (so the affordance is discoverable
  and the user learns what the allowlist is for).
- **Disabled — always off — when `request.risk_tier >= 4`**, with
  an explanatory sub-label: *"tier-4+ tools require explicit
  approval every time"*.
- **Default unchecked** at tier 3. Opt-in: the user ticks it only
  when they know they'll call this again with identical args.

On an approve-with-checkbox-ticked outcome, the UI gateway
(`TextualApprovalGateway`) calls
`SessionAllowlist.remember_user_approval(session_id, request)`
before returning the approval decision. Rejection never
populates the allowlist, regardless of checkbox state.

The hashed args signature (sha256 of sorted-JSON) keeps the
allowlist stable under key reordering but strict about value
identity — changing any argument value re-prompts. Session
archival drops the allowlist; process restart drops it; the
`approval_decisions` audit row is the durable record.

## Consequences

**Easier:**

- The iterative edit case is painless: approve the first
  `file_write`, tick the box, subsequent identical writes skip
  the modal.
- Users see the affordance immediately at tier-4 but can't
  accidentally bypass the "every time" semantic — the checkbox
  is visibly disabled with a reason.
- No separate tier-3 vs tier-4 modal — one modal, one rendering
  path, one test surface. The tier test is a boolean on
  `ApprovalRequest.risk_tier`.

**Harder:**

- A user who *genuinely* wants "skip this specific web_fetch call
  for the rest of the session" at tier-4 has no path short of
  demoting the tool, which requires a code change. This is
  deliberate: tier-4 means "you should want to look every time."
- The disabled checkbox takes visual real estate it can't
  actually use at tier-4. Minor UX cost; the explanatory label
  justifies the space.

**Ongoing cost:**

- The tier threshold (`4`) is defined in a module constant
  (`_TIER_4_PLUS`) in `_approval.py`. Future tier-taxonomy
  changes (e.g. splitting tier-4 into 4a/4b) would require
  revisiting this gate; link back to this ADR in any such
  commit.
- If a V2 affordance like "approve `file_write` under `src/**` for
  this session" lands, it supersedes the per-call checkbox for
  tier-3 but leaves the tier-4 lock-out unchanged. Write that
  ADR when the feature lands.
