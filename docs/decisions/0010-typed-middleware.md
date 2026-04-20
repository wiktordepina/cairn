# 0010 — Typed middleware over generic lifecycle hooks

**Date:** 2026-04-20
**Status:** accepted

## Context

The orchestrator needs extensibility seams for cross-cutting concerns:
memory injection into the request, tool-result sanitisation, approval
gating, observability. The natural anti-pattern is LangChain-style
generic `Callbacks` with twenty-odd lifecycle methods (`on_llm_start`,
`on_llm_end`, `on_tool_start`, etc.) — a surface area that grows
every release, where most methods are unused, and where stateful
interactions are easy to get wrong.

What we want:

- **Narrow, typed contracts**: each seam does one thing, with a
  specific input and output type.
- **Composable**: an ordered list of extensions at each seam, not
  a single pluggable callback.
- **Discoverable**: the list of seams IS the extensibility surface;
  no hidden hook names.

## Decision

Four seams, each a distinct `Protocol` with a single method:

- **`MessagePreparer`** — `prepare(request, ctx) -> ProviderRequest`.
  Rewrites the request before the provider sees it. Canonical uses:
  memory injection (memory brick), compaction (context-manager brick),
  prompt-cache markers.
- **`ToolApprover`** — `decide(request, ctx) -> ApprovalDecision`
  (outcome ∈ {APPROVE, REJECT, ESCALATE}). The chain runs in order;
  the first non-ESCALATE wins. An empty or all-ESCALATE chain falls
  through to the `ApprovalGateway` (a single protocol at the end,
  typically the UI).
- **`ResultTransformer`** — `transform(result, tool_call, ctx) -> result`.
  Rewrites a tool result before it hits the session. Canonical uses:
  spotlighting, Unicode stripping, secret redaction.
- **`UIEventObserver`** — `observe(event)`. Sync. Fire-and-forget.
  Cannot mutate events, cannot back-pressure. Failed observers are
  logged at ERROR and swallowed. Canonical uses: structured logging,
  metrics, cost dashboards.

Each seam is an ordered list at `Orchestrator.__init__`. The
orchestrator calls them inline at well-defined points in the turn
loop — no reflection, no dynamic dispatch.

## Consequences

**Easier:**

- The extensibility surface is enumerable. Four protocols, four points
  in the turn loop.
- Each protocol has a narrow, typed signature. Pyright catches misuse.
- Adding new behaviour follows a pattern: decide which seam it fits,
  implement the one-method protocol, register at construction.
- Observers that raise don't break the turn.

**Harder:**

- If a future cross-cutting concern doesn't fit one of the four seams,
  we add a fifth (with a new protocol) rather than stretching an
  existing one. That's a deliberate design constraint.
- No access to "orchestrator internals" through the middleware — every
  protocol gets a read-only `TurnContext` and the target of the
  transformation. A middleware wanting to peek at, say, the
  `turn_repo` would need an explicit new protocol dependency.

**Ongoing cost:**

- Writing a new preparer/approver/transformer/observer is ~20–50 LOC.
- Composing their order in the harness-assembly layer is a source of
  subtle bugs (spotlight-before-redact vs redact-before-spotlight).
  Mitigation: the defaults are opinionated and documented; most users
  won't reorder.
