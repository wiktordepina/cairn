# 0007 — Memory-space scoping enforced at the repository layer

**Date:** 2026-04-20
**Status:** accepted

## Context

Cairn has multiple session types with distinct memory semantics:

- `COMPANION` sessions share a single `companion` memory space.
- `PERSONA` sessions each have their own named space (or `None`).
- `EPHEMERAL` sessions have `memory_space = None` — no memory reads,
  no memory writes.

A single bug — a missing `WHERE memory_space = :space`, a call site
that forgets to filter — can leak personal memories into a work
persona, or surface ephemeral session content in the companion's
recall.

Enforcement options:

1. **At the call site.** Each caller is responsible for filtering.
   Auditable per-call; one mistake breaks isolation.
2. **In the model layer / DAO.** A wrapper that rejects queries
   lacking a space. Better, but still subject to "forgot to use the
   wrapper".
3. **At the repository layer, via API shape.** Methods that read
   session-scoped data *require* the space as a non-optional
   parameter. Cross-space methods exist but are named distinctly so
   they can't be called by accident.

This decision is not just about security — it's also about cognitive
load. The right default has to be safe.

## Decision

Repository methods that could leak across memory spaces fall into
two named groups:

- **Scoped reads.** `list_for_space(space=<str>, …)`. The caller must
  supply the space.
- **Cross-space reads.** `list_recent(…)`, `children_of(…)`. Named so
  they can't be typo'd into a scoped query. Used only where
  cross-space behaviour is explicitly intended (e.g. "all sessions I
  touched today, across all profiles and personas").

Writes similarly: session creation records the space; message append
is scoped via the session (a message cannot move between sessions);
memory writes (when the memory brick lands) will refuse to persist
without a space.

The invariant is not "every query filters correctly" but "the API
shape makes an unscoped query require a deliberately-named method
call".

## Consequences

**Easier:**

- Adding a new repository method: the signature tells you whether
  you're building a scoped read or a cross-space one.
- Auditing for leaks: grep for the cross-space method names. There
  should be a small, enumerable set of call sites.
- Onboarding: a new contributor can't accidentally write a
  memory-leak bug without typing the word `recent` or `across`.

**Harder:**

- Ergonomics of cross-space operations are slightly worse than they
  would be with a permissive API. Typing out the method name is the
  cost. Worth it.
- A feature that genuinely needs a new kind of cross-space query
  requires a new, deliberately-named method.

**Ongoing cost:**

- Discipline in naming: any new method that could leak across spaces
  must be named in a way that conveys that it can. The naming
  convention is part of the architecture contract.
