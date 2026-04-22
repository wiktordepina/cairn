# 0029 — Context budget is advisory, not hard

**Date:** 2026-04-22
**Status:** accepted

## Context

When the compactor drops everything the preserve-floor allows and
the request is *still* over the compaction budget, the harness has
three shapes of response available:

1. Silently continue — send the oversize request to the provider
   and hope.
2. Silently block — abort the turn internally, without user
   involvement.
3. Advise the user and let them decide.

Option 1 hides a real problem. Option 2 is unfriendly: our
`effective_budget` is intentionally conservative (default
`safety_margin = 2048`), which means "over the compaction
budget" does *not* imply "over the model's context window". A
request at `effective_budget + 1500` still has 500 tokens of
headroom below `context_window` and will very likely succeed.

## Decision

**The compaction budget is advisory.** When the preserve-floor is
hit and the surviving request is still over `effective_budget`,
the `TruncatingCompactor`:

1. Emits a `BudgetOverflowAdvisory` UI event with the numbers
   (projected tokens, context window, safety margin, overflow,
   whether the request still fits the hard context window).
2. `await`s a `BudgetOverflowGateway.decide(advisory, ctx)`.
3. On `"continue"` — emits `HistoryCompacted{reason="preserve_floor_hit"}`
   and returns the trimmed request. Provider call proceeds.
4. On `"terminate"` — raises `BudgetOverflowDeclined`. The
   orchestrator catches this in the turn loop, emits
   `TurnAborted(reason="user_declined_overflow")`, and archives
   the session via `SessionManager.archive`.

Two stub gateways ship:

- `AutoContinueOverflowGateway` — always returns `"continue"`;
  opt-in "I know what I'm doing".
- `AutoTerminateOverflowGateway` — always returns `"terminate"`;
  the **orchestrator default**, so headless and CI runs fail
  loudly rather than silently burn tokens.

The real interactive gateway (UI prompt, amber warning, warning
icon, per-session decision memory) lands with the UI brick.

## Consequences

- **Positive.** False positives from the conservative safety
  margin don't silently block work — the user gets to decide.
- **Positive.** True overflows aren't silently over-sent —
  `AutoTerminate` as the default means anything non-interactive
  that trips the floor fails loudly.
- **Positive.** Decision memory lives on the gateway (per-session
  scope), so a user who picks "continue" once doesn't get
  re-prompted every turn.
- **Positive.** Terminating *archives* the session rather than
  just aborting the turn. The next turn would hit the same
  overflow immediately, so archiving is the honest resolution.
- **Negative.** Adds a new collaborator protocol
  (`BudgetOverflowGateway`). One more thing a CLI bootstrap has
  to wire up, though with sensible defaults it's one line.
- **Negative.** The preparer is no longer a pure function of its
  inputs — it can now raise. The orchestrator has an extra
  `except` clause, and the preparer's protocol contract widens
  implicitly (documented here).

## Alternatives considered

**Silent overflow.** Rejected: compaction failures deserve
surface area, not silence.

**Silent block.** Rejected: the advisory budget is deliberately
conservative, so the common case where blocking fires is a false
positive. The user is in a better position to judge.

**Treat as `TurnIncomplete`.** Rejected during design review:
`TurnIncomplete` is a *generation* outcome ("model stopped
mid-tool-call"), a different failure shape. Reusing it would
muddy both events.

**Emit `TurnAborted` without archiving.** Rejected: the session
will hit the same overflow on its next turn. Archiving is the
honest resolution; the user can always start a fresh session.
