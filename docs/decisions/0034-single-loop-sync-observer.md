# 0034 — Single-loop invariant: synchronous UI observer, no thread hops

**Date:** 2026-04-24
**Status:** accepted

## Context

Cairn is a single-process app that runs several concurrent
workloads on asyncio:

- The Textual UI (event loop driving widget renders, input
  handling, modal lifecycle).
- The `Orchestrator.run_turn` coroutine — provider streaming,
  middleware, tool dispatch.
- The aiosqlite `Database` connection (`connect()` is async;
  transactions are awaited).
- The `ObservationExtractionQueue` background worker (long-lived
  task extracting observations between turns).
- The `ApprovalGateway` — a blocking
  `await push_screen_wait(ApprovalModal)` during tier-3+ tool
  approval.

The orchestrator exposes `UIEventObserver.observe(event)` as a
synchronous fan-out: tell me this happened, I'll update my
widgets. Two shapes were available:

1. **Run the UI on its own thread** (common pattern in GUI apps
   over a compute thread). Orchestrator → observer call crosses
   the boundary via `App.call_from_thread(method, *args)` which
   marshals onto the UI loop. Requires every observer branch to
   know "I'm crossing a thread" and the approval gateway needs a
   second thread-safe `asyncio.Event` to bridge back.
2. **Run everything on one event loop**, including the
   orchestrator, database, extraction worker, and UI. The
   observer is a plain sync call from the orchestrator's stack
   into the widget tree. The approval gateway is a direct
   `await push_screen_wait(...)`.

The UI brick's `_bootstrap.launch()` assembles the full
collaborator graph (config → persistence → providers → memory →
tools → orchestrator → app) on one loop and drives
`App.run_async()`. Every coroutine in the system is thus
reachable from a single `asyncio.get_running_loop()`.

## Decision

Single-loop invariant: all Cairn async code shares one event
loop. The UI observer (`TextualUIEventObserver`) dispatches
directly to `SessionScreen` methods — no `call_from_thread`, no
queue, no executor.

Specifically:

- `TextualUIEventObserver.observe(event)` is called on the same
  loop Textual runs on, from the same coroutine that produced
  the event. The `match` block invokes widget methods in-place.
- Exceptions inside an observer branch are caught and logged;
  never re-raised. A malformed event must not break the turn
  loop that produced it.
- `TextualApprovalGateway.request_approval` awaits
  `push_screen_wait` directly. The orchestrator's approval
  middleware awaits the gateway. Everything is one call stack.
- The app and the orchestrator are wired with a circular
  dependency (gateway needs app, app wants orchestrator), which
  is resolved by constructing the app first and swapping the
  real gateway + observer onto the orchestrator
  post-construction.
- The aiosqlite `Database` and the extraction queue's worker
  task both live on this same loop. No `asyncio.run_coroutine_
  threadsafe` anywhere.

## Consequences

**Easier:**

- Stack traces are linear. A failing tool call traces from the
  tool runner → orchestrator → provider and back into the
  observer that rendered the banner. No "this happened on the
  UI thread" gaps.
- No thread-safety burden on widget mutations. Widgets, the
  `tool_calls` table, and the observation log are all mutated
  from one loop.
- Approval UX is trivially correct: the orchestrator awaits the
  gateway awaits the modal awaits the user. No bridging
  primitives.
- The pilot tests (`App.run_test()`) drive the entire stack —
  including the orchestrator — on a synthetic but real event
  loop, so widget behaviour under real event flow is testable.

**Harder:**

- Any truly CPU-bound work (none today, but hypothetically
  embedding models, big regex sweeps, etc.) would block the UI.
  The escape hatch is `asyncio.to_thread` / a
  `ProcessPoolExecutor`, both fine — but we pay the cost
  deliberately at the call site instead of blanket-threading the
  whole orchestrator.
- Third-party libraries that assume their own loop (some SDKs
  spawn background loops) can step on us. We've mitigated by
  using async-native libraries throughout (`anyio`, `aiosqlite`,
  `anthropic.AsyncAnthropic`, `openai.AsyncOpenAI`); future
  additions need to respect the invariant.
- A crash in the event loop is a crash in the whole app. No
  "UI stays up, backend died" mode. That's acceptable for a
  personal tool — the UI has no useful state after the
  orchestrator dies — and the rotating file logger captures the
  traceback.

**Ongoing cost:**

- The invariant is undocumented in the code outside a couple of
  docstring asides; this ADR is the canonical reference. New
  code that introduces threads or secondary loops should cite
  this ADR in its review.
- The `# noqa: BLE001` on observer exception-handling is
  load-bearing — narrowing it would let a typo-level bug in one
  widget break every subsequent event. Resist the lint
  temptation.
