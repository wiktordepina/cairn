# Architecture Decision Records

Small, dated records of non-obvious decisions made while building cairn.
The format is [Michael Nygard's ADR
shape](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions):
*Context → Decision → Consequences*.

## Why we keep these

Cairn's architecture has a lot of "it could have been X or Y — we chose
Y because of Z". If Z isn't written down, future contributors (including
future Wiktor) will re-litigate the decision from first principles, or
quietly diverge from it. ADRs are the cheapest form of institutional
memory that survives people.

## What counts as "non-obvious"

An ADR is worth writing when:

- A reasonable engineer could have chosen differently.
- The decision has cross-cutting consequences (touches more than one
  brick).
- The reasoning depends on context that won't be visible in the code
  (e.g. security requirements, performance measurements, historical
  incidents, personal preferences).

Settled decisions don't need ADRs. We don't have an ADR for "use
Python" or "use SQLite" — those are load-bearing but uncontested.

## Format

Each ADR is a separate file named `NNNN-kebab-case-title.md`:

```markdown
# NNNN — Title

**Date:** YYYY-MM-DD
**Status:** proposed | accepted | superseded by NNNN

## Context

What is the issue motivating a decision? What constraints apply? What's
been tried or considered?

## Decision

What did we decide? State it plainly. One or two paragraphs is usually
enough.

## Consequences

What becomes easier? What becomes harder? What have we given up? What
ongoing costs does this impose?
```

## Numbering and status

- Numbers are sequential and never reused.
- Dates are when the ADR was *recorded*, not necessarily when the
  decision was first made. For retroactive ADRs, the ADR may post-date
  the implementation.
- Once an ADR is `accepted`, it is **never edited** except to correct
  typos or clarify wording. If a decision is reversed, write a new ADR
  marking the old one `superseded`.

## Index

| # | Title | Status |
|---|---|---|
| [0001](0001-per-profile-sqlite.md) | Per-profile SQLite database | accepted |
| [0002](0002-three-tier-config-merge.md) | Three-tier config merge (user / project / local) | accepted |
| [0003](0003-secret-ref-schemes.md) | `SecretRef` with four schemes (`keyring`, `env`, `prompt`, `literal`) | accepted |
| [0004](0004-hand-rolled-migrations.md) | Hand-rolled SQL migrations over an ORM | accepted |
| [0005](0005-per-provider-adapters.md) | Per-provider adapters over a unified client | accepted |
| [0006](0006-role-based-model-selection.md) | Role-based model selection | accepted |
| [0007](0007-memory-space-at-repo-layer.md) | Memory-space scoping at the repository layer | accepted |
| [0008](0008-tool-call-state-column.md) | Tool-call state as a column, not an event stream | accepted |
| [0009](0009-protocol-stubs.md) | Protocol-shaped collaborators with default stubs | accepted |
| [0010](0010-typed-middleware.md) | Typed middleware over generic lifecycle hooks | accepted |
| [0011](0011-explicit-turn-state-machine.md) | `turns` table with an explicit state machine | accepted |
| [0012](0012-no-fk-on-turn-id.md) | No foreign-key constraint on `turn_id` columns | accepted |
| [0013](0013-tier-taxonomy.md) | Tier taxonomy for tool risk (0-4 in V1) | accepted |
| [0014](0014-tool-decorator.md) | `@tool` decorator over a class hierarchy | accepted |
| [0015](0015-session-allowlist-exact-match.md) | Session approval allowlist uses exact args-signature match | accepted |
| [0016](0016-no-shell-in-v1.md) | No shell / code-execution tool in V1 | accepted |
| [0017](0017-ssrf-ip-pinning-deferred.md) | SSRF defence: IP pinning deferred in V1 | accepted |
