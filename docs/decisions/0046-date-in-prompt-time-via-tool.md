# 0046 — Date in system prompt; time via the `now` tool

**Date:** 2026-04-26
**Status:** accepted

## Context

On 2026-04-25 a real session surfaced the bug that motivates
this ADR: the model thought it was the middle of the night and
declined a request on that basis, when in fact it was mid-
afternoon. Nothing in the cairn-side context disambiguated the
wall-clock — the model's training-data prior dominated.

Two questions:

1. Should cairn ground the model in current time?
2. If so, where — the system prompt, a tool, or both?

The system-prompt path is cheap (one cacheable line) and always
present. The tool path is on-demand but doesn't pollute every
turn's context. The tradeoff hinges on prompt-cache stability:
a minute-precision wall-clock in the prompt would invalidate the
cache every minute, defeating the cache benefit cairn just spent
the prompt-caching brick (0.13.0) building.

## Decision

**Hybrid: date in the system prompt, time via a tool.**

### Date in the system prompt (cacheable)

A new `<today>` segment, placed between `<identity>` and
`<user_context>`, renders one line:

```
Today is 2026-04-26 (Europe/London).
```

- Date-only, so the segment is stable for the user's entire
  calendar day. Prompt cache survives normal use; only midnight-
  local invalidates it.
- The segment lives inside the cacheable profile-stable section
  alongside identity / user-context / conventions / memory.
- Computed from the orchestrator's `Clock` (so tests pin it via
  `FrozenClock`) and the new `[locale.timezone]` profile field
  (default: host timezone).

### `now` tool for minute precision (on-demand)

A tier-0 read-only tool, auto-approved by the existing
`AutoApproveReadOnly` rule:

```python
make_now(clock, timezone_name=...) -> Tool
# returns: {"iso": "2026-04-26T15:34:21+01:00", "tz": "Europe/London"}
```

The model calls it when it needs minute precision: log analysis,
scheduling, "exactly how long ago" questions. The tool result
isn't part of the cacheable prefix, so it doesn't break cache
on subsequent turns.

This mirrors how Claude Code itself does it (date in system
prompt, time deferred to tools) and the broader 2026 trend
toward least-privilege live context via tools.

### Profile timezone

A new `[locale.timezone]` field on `ProfileConfig`. IANA name
(e.g. `"Europe/London"`). Falls back to the host's local
timezone when unset, so out-of-the-box behaviour is sensible
without requiring config edits. The same field is used by the
`/cost` window boundaries (today / MTD; ADR 0047), keeping
"what counts as today" consistent across the app.

## Consequences

- The system prompt grows by ~one line. The prefix-cache layout
  is unchanged because the segment lives in the existing
  profile-stable block.
- DST transitions and travel cause the date stamp to update on
  the next turn after the timezone shifts. Acceptable — these
  are rare events, the staleness window is one turn, and the
  model can call `now` if precision matters.
- The `now` tool is auto-approved at tier-0 + `side_effects=
  "none"` — same approval bucket as `file_read` would be if it
  were free of disk I/O. Operators can still gate it via the
  `[tools]` allowlist.
- Tests pin both the system-prompt segment and the tool's output
  via `FrozenClock` and an explicit `timezone_name`.

## Alternatives considered

- **Time-of-day in the system prompt** (e.g. "It is 15:34
  Europe/London"). Rejected — invalidates the prompt cache
  every minute. Even at a five-minute granularity ("It is
  approximately 15:30"), cache rate would drop sharply.
- **Tool only (no system-prompt segment)**. Rejected — the
  date is the load-bearing signal. The model needs it for
  basic temporal reasoning ("the last release was 6 days
  ago"); making it on-demand would mean every such turn pays
  a tool round-trip.
- **System-prompt segment only (no tool)**. Acceptable for
  V1 ergonomics but blocked the minute-precision use cases
  the original observation surfaced.
- **Inject time on first user message of the session**.
  Half-cached: still invalidates whenever a session starts in
  a new minute, and breaks the "every turn has the same
  prefix" invariant the cache relies on.
