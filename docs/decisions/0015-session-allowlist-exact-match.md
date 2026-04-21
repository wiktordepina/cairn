# 0015 — Session approval allowlist uses exact args-signature match

**Date:** 2026-04-21
**Status:** accepted

## Context

Tier 3 tools (mutating filesystem operations, delegation) require
approval the first time the model invokes them in a session. The
question is what happens on the second, third, fourth call.

Three shapes considered:

1. **Always re-prompt.** Safe, but adds friction to every step of
   an iterative edit session. Users hit prompt fatigue and either
   reflexively approve (defeating the point) or abandon the session.
2. **Approve the tool for the rest of the session.** "Let
   `file_write` go through for this session" is fast, but the
   granularity is wrong — approving one write to `src/foo.py` should
   not auto-approve writes to `package.json` or `.env`.
3. **Approve an exact `(tool_name, args)` tuple.** Re-prompt on any
   argument change. The user approves a specific action; repeats of
   that specific action go through.

Option 3 gives the right default. The granularity matches the
user's mental model: the model is asking to do a specific thing, the
user approves that thing, the same thing can happen again without
asking.

A fourth alternative — "approve the tool for a set of paths the
user specifies" — is real and will matter eventually ("trust
`file_write` everywhere under `src/`"). It's also more UI than
V1 can afford. The UI brick can add it as a follow-on affordance
without changing the allowlist's underlying contract.

## Decision

`SessionAllowlist` caches `(tool_name, args_signature)` tuples,
where `args_signature = sha256(sorted_json(args))`. On the second
call with an identical tool name and identical argument values, the
allowlist returns `APPROVE` from the approver chain. On any change
to the args — different path, different mode, any bit different —
the chain falls through to the gateway and the user is prompted
again.

The hash is over the canonicalised JSON (`sort_keys=True`), so
argument ordering has no effect; only value differences re-prompt.
The cache is per-session — archiving a session drops its
allowlist. Restarting the process also drops it; allowlists are
deliberately in-memory, not persisted. The `approval_decisions`
table holds the audit trail; the allowlist is a runtime convenience
on top of that record.

Population is explicit: the harness-assembly layer calls
`SessionAllowlist.remember_user_approval(session_id=..., request=...)`
after receiving a `decided_by="user"` result from the gateway. The
allowlist doesn't snoop the approval chain.

## Consequences

**Easier:**

- The friction model is right: approve a thing once, the same thing
  is free thereafter. Different things still prompt.
- No state-recovery logic at startup — nothing is persisted.
- The audit trail (`approval_decisions` rows) is independent of the
  allowlist's runtime cache.

**Harder:**

- "I want to trust `file_write` everywhere inside `src/`" has no
  V1 answer. A user who wants that granularity either pre-approves
  each path individually or waits for the UI brick to add a
  trust-scope affordance.
- Two semantically identical calls with different argument shapes
  (whitespace in a JSON-valued arg, for example) would be treated as
  different. This is rare in practice and the re-prompt is the
  conservative default.

**Ongoing cost:**

- `SessionAllowlist` lives in-process memory. For long-running
  sessions across days, nothing prevents the allowlist growing
  arbitrarily. Sessions are archived periodically and the cache is
  cleared then. If this turns out to matter, a per-session bound
  (LRU, time-based) is a local change.
