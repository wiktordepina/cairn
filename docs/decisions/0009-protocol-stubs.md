# 0009 — Protocol-shaped collaborators with default stubs

**Date:** 2026-04-20
**Status:** accepted

## Context

The orchestrator has a lot of collaborators: context assembly, memory,
tools, extraction, approval, cost tracking, model resolution, providers,
persistence. In the obvious implementation, the orchestrator takes
concrete types, imports them, and calls their methods. That creates a
hard dependency on each of those bricks existing in its final shape
before the orchestrator can ship.

Two consequences of that approach:

1. **Blocking.** The orchestrator can't be written (let alone tested)
   before every downstream brick is complete. The "pivotal brick" ends
   up being the last one delivered.
2. **Coupling.** Changes to a collaborator's internal API ripple through
   the orchestrator even when the collaborator's *role* hasn't
   changed.

Alternatives:

- **Protocols + stubs.** Every collaborator is a `Protocol`; a default
  stub implementation ships in `cairn.orchestrator` for each. Real
  implementations plug in by name at construction.
- **Interfaces + DI container.** Same idea, heavier machinery.
- **Concrete base classes.** Some of the same benefit with less rigour;
  introduces inheritance, which we wanted to avoid.

## Decision

Every orchestrator dependency is a `Protocol` (`@runtime_checkable`
where practical). Default stubs ship in `cairn.orchestrator._stubs`:

- `NullMemoryService`, `NullExtractionQueue` — safely ignored.
- `EmptyToolRegistry` + `RaisingToolRunner` — paired so the runner
  can never be called.
- `AutoApproveGateway`, `DenyAllGateway` — context-appropriate
  defaults.
- `MinimalContextManager` — passes through history.

The orchestrator constructor enforces nothing at the type level about
which implementation is passed; the harness-assembly layer (CLI) wires
real ones in production.

Test fakes (`FakeProvider`, `RecordingToolRunner`, `SpyMemoryService`,
etc.) live under `tests/orchestrator/_fakes.py`; they are test-only and
not part of the shipped package.

## Consequences

**Easier:**

- The orchestrator shipped as a complete, tested brick before tools,
  memory, or the UI existed. Real collaborators plug in when their
  bricks land.
- Tests run in milliseconds because they never touch real providers or
  UIs. The whole 15-test orchestrator suite runs in under a second.
- Swapping a collaborator — e.g. a different memory retriever —
  requires exactly one change at construction, no orchestrator edits.

**Harder:**

- A mis-wired orchestrator (stub where a real impl was expected)
  silently degrades. Mitigation: the CLI's harness-assembly fails loud
  if it finds a stub where it was supposed to wire something real.
- Protocol drift: if the protocol and the implementation disagree,
  pyright catches it at build time; runtime duck-typing is a weaker
  guarantee.

**Ongoing cost:**

- Adding a new collaborator means: a new protocol + a new stub + a
  constructor parameter. Roughly 50 LOC of overhead per new
  collaborator. Worth it.
