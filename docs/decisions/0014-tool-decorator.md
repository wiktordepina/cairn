# 0014 — `@tool` decorator over a class hierarchy

**Date:** 2026-04-21
**Status:** accepted

## Context

Every tool implements the same narrow `Tool` protocol: a handful of
metadata properties plus an async `invoke(args, ctx)`. Two natural
ways to write that:

1. **A class per tool** — subclass a `ToolBase` (or implement the
   `Tool` protocol directly), declare metadata as class attributes,
   implement `invoke` as a method.
2. **A decorator on an async function** — `@tool(...)` wraps a
   function with metadata captured in the decorator arguments, and
   produces a protocol-conforming instance.

Both are viable. Tools are small: a typical built-in is 30-80 LOC
plus a Pydantic `args_model`. Classes add boilerplate without a
corresponding gain — there's no inheritance hierarchy worth having,
and `self` is empty of interesting state for most tools. The few
cases that need instance state (`DelegationTool` with its injected
collaborators, a hypothetical stateful connector) are a handful, and
writing those as explicit classes is fine — the decorator is the
90-percent path, not the only path.

The decorator also earns its keep beyond brevity:

- **Schema generation.** The Pydantic `args_model` is the single
  source of truth for argument shape. The decorator calls
  `args_model.model_json_schema()` to produce the JSON schema the
  provider needs. No duplicate declaration.
- **Validation at decoration time.** The tier/side-effects
  consistency rules ([ADR 0013](0013-tier-taxonomy.md)) fire at
  module-import time when the decorator runs — any misconfiguration
  is a `ValueError` during CI, not a runtime surprise.
- **Parsed-args at invoke time.** The decorator calls
  `args_model.model_validate(args)` before passing to the user
  function, so the body works with typed, validated data rather than
  raw `dict[str, object]`.

## Decision

V1 tools use the `@tool(...)` decorator:

```python
@tool(
    name="file_read",
    description="...",
    risk_tier=1,
    side_effects="read",
    timeout_s=10.0,
    args_model=FileReadArgs,
)
async def file_read(args: FileReadArgs, ctx: TurnContext) -> str:
    ...
```

The decorator returns a `_DecoratedTool` instance that satisfies the
`Tool` protocol. It is not a global registration — the CLI imports
tool modules and explicitly builds `DefaultToolRegistry` from the
resulting instances. "Register by import side effect" was rejected
as too implicit.

When a tool needs injected collaborators (a `WorkspaceSandbox` for
filesystem tools, a `SessionManager` for delegation), the tool
module exposes a `make_<tool>(collaborator, ...)` factory that
returns the decorated function. The decorator captures the collaborator
in a closure; the CLI calls the factory once at startup.

Tools that genuinely need instance state (V1 has none; V2's
`DelegationTool` is the first candidate) remain free to implement
the `Tool` protocol with a regular class — the decorator is the
default, not a straitjacket.

## Consequences

**Easier:**

- Tools are short, readable, and obviously correct. The metadata is
  declared adjacent to the body that honours it.
- Schema drift is impossible — the JSON schema is generated from
  the Pydantic model every time.
- Validation fires at decoration time, so a miswired tool fails the
  import, not the turn.
- Testing a tool is calling `tool.invoke({...}, ctx)` — no class
  instantiation ceremony.

**Harder:**

- "Find all tools" via IDE "find implementations" doesn't work the
  same way it would for `class FooTool(ToolBase): ...` — the
  `@tool` decorator is not searchable as a base class. Mitigation:
  the built-in tools are catalogued in `docs/tools.md` and the
  registry is the authoritative list at runtime.
- A decorated tool carries its metadata on the closure, not a class
  attribute. Runtime introspection (reading `tool.risk_tier` etc.)
  works identically, but static tools that IDEs offer for class
  hierarchies (auto-generating docstrings from the class, etc.) do
  not apply.

**Ongoing cost:**

- The decorator's validation logic is one file (`_decorator.py`)
  that every tool depends on. If a tier-validation bug ever ships,
  every tool inherits it. Mitigation: exhaustive unit tests on the
  decorator itself, fixed and easy to reason about.
